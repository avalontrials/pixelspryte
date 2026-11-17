"""
PixelSpryte LLM Planner
Generates EDL plans via KoboldCPP (local) or OpenAI-compatible APIs (DeepSeek, etc.).
No LangChain dependency — just HTTP requests.
"""

import json
import os
import time
from pathlib import Path
from typing import List, Dict, Optional

import requests

try:
    # Use the Windows certificate store for TLS verification.
    # Required on this machine: Avast Web Shield intercepts HTTPS to
    # api.deepseek.com and re-signs with its own root cert, which is in
    # the Windows store but not in certifi's bundle.
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

try:
    from dotenv import load_dotenv
    # Load .env from the project root (parent of core/)
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass  # dotenv optional — key can still come from config or real env vars


def _resolve_api_key(llm_cfg: dict) -> str:
    """
    Resolve the API key. Priority:
      1. Explicit key in config (discouraged — use .env)
      2. DEEPSEEK_API_KEY from .env / environment
    """
    key = (llm_cfg.get("api_key") or "").strip()
    if key and key != "YOUR_DEEPSEEK_API_KEY_HERE":
        return key
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


# ─── Backend Dispatch ────────────────────────────────────────────────

def _call_llm(messages: list, cfg: dict) -> str:
    """
    Route to the correct backend based on cfg["llm"]["backend"].
    Supports: "koboldcpp", "openai_compatible"
    """
    backend = cfg.get("llm", {}).get("backend", "koboldcpp")

    if backend == "openai_compatible":
        return _call_openai_compatible(messages, cfg)
    else:
        # KoboldCPP uses a flat prompt, not chat messages
        prompt = _messages_to_prompt(messages)
        return _call_koboldcpp(prompt, cfg)


def _messages_to_prompt(messages: list) -> str:
    """Convert chat messages to a Qwen-style prompt string."""
    parts = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        parts.append(f"<|im_start|>{role}\n{content}\n<|im_end|>")
    parts.append("<|im_start|>assistant\n")
    return "\n".join(parts)


# ─── KoboldCPP Backend ──────────────────────────────────────────────

def _call_koboldcpp(prompt: str, cfg: dict) -> str:
    """Send a generation request to KoboldCPP API."""
    api_url = cfg.get("llm", {}).get("api_url", "http://localhost:5001")
    temperature = cfg.get("llm", {}).get("temperature", 0.3)
    max_tokens = cfg.get("llm", {}).get("max_tokens", 2048)

    payload = {
        "prompt": prompt,
        "max_length": max_tokens,
        "temperature": temperature,
        "top_p": cfg.get("llm", {}).get("top_p", 0.9),
        "top_k": cfg.get("llm", {}).get("top_k", 40),
        "rep_pen": cfg.get("llm", {}).get("rep_pen", 1.1),
        "stop_sequence": ["\n\n\n", "```"]
    }

    try:
        response = requests.post(
            f"{api_url}/api/v1/generate",
            json=payload,
            timeout=120
        )
        response.raise_for_status()
        result = response.json()
        return result["results"][0]["text"].strip()

    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Cannot connect to KoboldCPP at {api_url}.\n"
            "Make sure KoboldCPP is running with your model loaded."
        )
    except requests.exceptions.Timeout:
        raise RuntimeError("KoboldCPP request timed out (120s).")
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected KoboldCPP response format: {e}")


# ─── OpenAI-Compatible Backend (DeepSeek, etc.) ─────────────────────

def _call_openai_compatible(messages: list, cfg: dict) -> str:
    """
    Send a chat completion request to any OpenAI-compatible API.
    Works with: DeepSeek, OpenAI, Together, OpenRouter, Groq, etc.
    """
    llm_cfg = cfg.get("llm", {})
    api_url = llm_cfg.get("api_url", "https://api.deepseek.com/v1")
    api_key = _resolve_api_key(llm_cfg)
    model = llm_cfg.get("model_name", "deepseek-chat")
    temperature = llm_cfg.get("temperature", 0.2)
    max_tokens = llm_cfg.get("max_tokens", 4096)

    if not api_key:
        raise RuntimeError(
            "API key not configured.\n"
            "Set DEEPSEEK_API_KEY in C:\\PixelSpryte\\.env\n"
            "Get a DeepSeek key at: https://platform.deepseek.com"
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"}  # Force JSON output
    }

    # Normalize URL: ensure it ends with /chat/completions
    endpoint = api_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = f"{endpoint}/chat/completions"

    # Log prompt size for debugging
    prompt_chars = sum(len(m.get("content", "")) for m in messages)
    print(f"  Prompt size: ~{prompt_chars} chars ({prompt_chars // 4} est. tokens)")

    try:
        response = _post_with_retry(endpoint, headers, payload)
        result = response.json()

        # Debug: log full response structure (minus content) for troubleshooting
        choice = result.get("choices", [{}])[0]
        finish_reason = choice.get("finish_reason", "unknown")
        usage = result.get("usage", {})
        print(f"  API finish_reason: {finish_reason}")
        if usage:
            print(f"  Tokens - prompt: {usage.get('prompt_tokens', '?')}, "
                  f"completion: {usage.get('completion_tokens', '?')}")

        content = choice.get("message", {}).get("content")

        # Handle null/empty content
        if content is None:
            # Some models put content in reasoning_content for thinking mode
            reasoning = choice.get("message", {}).get("reasoning_content", "")
            if reasoning:
                print(f"  Note: Model used thinking mode ({len(reasoning)} chars reasoning)")
                # Try to extract JSON from reasoning content
                content = reasoning
            else:
                print(f"  WARNING: API returned null content (finish_reason={finish_reason})")
                print(f"  Full response keys: {list(result.keys())}")
                print(f"  Choice keys: {list(choice.get('message', {}).keys())}")
                return ""

        return content.strip()

    except (KeyError, IndexError, ValueError) as e:
        raise RuntimeError(f"Unexpected API response format: {e}")


def _post_with_retry(endpoint: str, headers: dict, payload: dict,
                     attempts: int = 3, timeout: int = 300):
    """
    POST with retry + backoff. Retries on connection errors, timeouts,
    and 5xx/429 responses. Raises RuntimeError after the last attempt
    (caller falls back to the heuristic editor).
    """
    backoff = [2, 5]
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(endpoint, headers=headers,
                                     json=payload, timeout=timeout)
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            else:
                # Non-retryable HTTP errors (401, 400...) raise immediately
                if not response.ok:
                    try:
                        detail = response.json().get("error", {}).get(
                            "message", response.text[:200])
                    except Exception:
                        detail = response.text[:200]
                    raise RuntimeError(f"API error ({response.status_code}): {detail}")
                return response
        except requests.exceptions.ConnectionError:
            last_error = f"Cannot connect to {endpoint} (no internet?)"
        except requests.exceptions.Timeout:
            last_error = f"Request timed out ({timeout}s)"

        if attempt < attempts:
            wait = backoff[min(attempt - 1, len(backoff) - 1)]
            print(f"  API attempt {attempt}/{attempts} failed: {last_error}")
            print(f"  Retrying in {wait}s...")
            time.sleep(wait)

    raise RuntimeError(f"API failed after {attempts} attempts: {last_error}")


# ─── Status Checks ──────────────────────────────────────────────────

def check_llm_status(cfg: dict) -> bool:
    """Check if the configured LLM backend is reachable."""
    backend = cfg.get("llm", {}).get("backend", "koboldcpp")

    if backend == "koboldcpp":
        return _check_koboldcpp(cfg)
    elif backend == "openai_compatible":
        return _check_openai_compatible(cfg)
    return False


def _check_koboldcpp(cfg: dict) -> bool:
    """Check if KoboldCPP is running."""
    api_url = cfg.get("llm", {}).get("api_url", "http://localhost:5001")
    try:
        resp = requests.get(f"{api_url}/api/v1/model", timeout=5)
        if resp.status_code == 200:
            model_info = resp.json()
            print(f"  KoboldCPP model: {model_info.get('result', 'unknown')}")
            return True
    except requests.exceptions.ConnectionError:
        return False
    return False


def _check_openai_compatible(cfg: dict) -> bool:
    """Check if the API key is configured (can't easily ping without cost)."""
    llm_cfg = cfg.get("llm", {})
    api_key = _resolve_api_key(llm_cfg)
    model = llm_cfg.get("model_name", "unknown")
    api_url = llm_cfg.get("api_url", "")

    if api_key:
        print(f"  API configured: {model} @ {api_url}")
        return True
    else:
        print("  API key not found (set DEEPSEEK_API_KEY in .env).")
        return False


# Keep old name as alias for backwards compatibility
def check_koboldcpp_status() -> bool:
    """Legacy alias. Use check_llm_status(cfg) instead."""
    return _check_koboldcpp({"llm": {"api_url": "http://localhost:5001"}})


# ─── EDL Generation ─────────────────────────────────────────────────

def build_edl_with_llm(
    segments: List[Dict],
    target_minutes: int,
    structure: List[str],
    allow_zoom: bool = True,
    allow_jump: bool = True,
    captions: str = "styled",
    template: str = "standard",
    api_url: Optional[str] = None,
    cfg: Optional[dict] = None,
    script_reference: Optional[str] = None,
    **kwargs
) -> dict:
    """
    Generate EDL JSON from analyzed segments using the configured LLM backend.

    Strategy: Ask the LLM to identify CUT ranges (much smaller output than
    listing every keep segment). Then build keep_segments from the gaps.

    Returns:
        Dict matching EDLPlan schema.
    """
    # Build config if not provided (backwards compatibility)
    if cfg is None:
        cfg = {
            "llm": {
                "backend": "koboldcpp",
                "api_url": api_url or "http://localhost:5001",
                "temperature": 0.3,
                "max_tokens": 2048
            }
        }

    backend = cfg.get("llm", {}).get("backend", "koboldcpp")
    is_cloud = backend == "openai_compatible"

    # Segment limit depends on backend context window
    seg_limit = 500 if is_cloud else 100
    transcript_limit = 300 if is_cloud else 150

    # Compact segment data for the prompt
    compact_segments = []
    for seg in segments[:seg_limit]:
        compact_segments.append({
            "idx": seg["idx"],
            "start": round(seg["start"], 1),
            "end": round(seg["end"], 1),
            "dur": round(seg["end"] - seg["start"], 1),
            "text": seg.get("transcript_excerpt", "")[:transcript_limit],
        })

    # Total duration of source material
    total_src_dur = 0.0
    if segments:
        total_src_dur = max(s["end"] for s in segments)

    # Editing style: "retention" (YouTube), "vlog" (family), or webinar
    # (implied by script_reference)
    style = (cfg.get("edit_defaults") or {}).get("style", "retention")

    # Build prompts
    system_prompt = _build_system_prompt(
        target_minutes, structure, allow_zoom, allow_jump, captions,
        script_reference=script_reference, style=style
    )
    broll_enabled = (cfg.get("edit_defaults") or {}).get("broll", "none") != "none"
    zoom_max = (cfg.get("edit_defaults") or {}).get("zoom_max_scale", 1.15)

    user_prompt = _build_user_prompt(
        compact_segments, target_minutes, structure, captions, total_src_dur,
        allow_zoom=allow_zoom, broll_enabled=broll_enabled
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    # Override max_tokens for this call -- cut list needs room
    orig_max = cfg.get("llm", {}).get("max_tokens", 4096)
    cfg.setdefault("llm", {})["max_tokens"] = max(orig_max, 16384)

    # Call LLM
    try:
        raw_response = _call_llm(messages, cfg)
    except RuntimeError as e:
        print(f"  LLM unavailable: {e}")
        print("  Falling back to heuristic editor...")
        cfg["llm"]["max_tokens"] = orig_max
        return _fallback_edl(segments, target_minutes, captions)
    finally:
        cfg["llm"]["max_tokens"] = orig_max

    # Debug: log raw response for troubleshooting
    _debug_log_response(raw_response, cfg)

    # Parse the cut list from the LLM
    cuts_json = _parse_json_response(raw_response)

    if cuts_json and "cuts" in cuts_json:
        # Build keep_segments from the gaps between cuts
        edl = _cuts_to_edl(cuts_json, segments, total_src_dur, captions,
                           zoom_max=zoom_max)
        total_dur = sum(
            s["keep_end"] - s["keep_start"] for s in edl["keep_segments"]
        )
        print(f"  LLM identified {len(cuts_json['cuts'])} cut regions")
        print(f"  EDL generated: {len(edl['keep_segments'])} keep segments, {total_dur/60:.1f} min")
        return edl
    elif cuts_json and "keep_segments" in cuts_json:
        # LLM returned old-style keep list (still works if valid)
        total_dur = sum(
            s.get("keep_end", 0) - s.get("keep_start", 0)
            for s in cuts_json.get("keep_segments", [])
        )
        if "cut_segments" not in cuts_json:
            cuts_json["cut_segments"] = []
        print(f"  EDL generated: {len(cuts_json['keep_segments'])} segments, {total_dur/60:.1f} min")
        return cuts_json
    else:
        print("  LLM returned invalid JSON - using fallback")
        print(f"  (Raw response preview: {raw_response[:200]}...)")
        return _fallback_edl(segments, target_minutes, captions)


# ─── Prompt Builders ─────────────────────────────────────────────────

def _build_system_prompt(
    target_minutes, structure, allow_zoom, allow_jump, captions,
    script_reference=None, style="retention"
) -> str:
    """
    Build the system prompt.
    Webinar-aware if script_reference is provided; otherwise picks the
    editing persona from `style` ("retention" or "vlog").
    """

    if script_reference:
        # Include more of the script (up to 8000 chars) for better matching
        script_excerpt = script_reference[:8000]
        if len(script_reference) > 8000:
            script_excerpt += "\n[... script continues ...]"

        return f"""You are an expert video editor specializing in long-form webinar cleanup.

You have the ORIGINAL SCRIPT and a timestamped transcript of the recording.
Your job: identify the REGIONS TO CUT. Everything not listed as a cut is kept.

CUT CRITERIA (what to remove):
- Filler words: um, uh, like, you know, so yeah, basically, actually
- Dead air: silence longer than 3 seconds
- False starts: speaker restarts a sentence ("wait let me...", repeated phrases)
- Repeated takes: same content said twice (keep the better take)
- Off-script tangents that don't add value
- Mic adjustments, paper shuffling, technical issues

PRESERVE (never cut these):
- All intentional content matching or closely following the script
- Intentional dramatic pauses (2-3 seconds)
- ALL objection handling content -- precision matters there
- ALL closing content -- never rush the close
- Bridge sentences between sections (open loops)
- Section structure: {', '.join(structure)}

The speaker recorded in two sessions (camera overheated). Treat as one continuous take.
Target final duration after cuts: ~{target_minutes} minutes.

ORIGINAL SCRIPT (for reference):
{script_excerpt}

OUTPUT: Return ONLY a JSON object listing the regions to CUT. See user message for format."""

    elif style == "vlog":
        return f"""You are a family vlog editor.

Your job: identify the REGIONS TO CUT from home video footage.
Everything not listed as a cut is kept, in chronological order.

KEEP (the heart of a family vlog):
- Genuine laughter, candid reactions, kids being themselves
- Milestones and "firsts", even if imperfectly filmed
- Natural conversations and interactions
- Short scene-setting shots (arriving somewhere, scenery, meals)

CUT:
- Dead air where nothing happens and nobody speaks
- Long uneventful stretches (walking, waiting, driving) -- keep a few
  seconds as a transition, cut the rest
- Repeated attempts at the same staged shot (keep the best one)
- Camera fumbling, pocket footage, accidental recordings

STYLE:
- Warm and unhurried. Do NOT over-cut -- this is not a highlights reel.
- Let moments breathe; keep a beat before and after laughter or reactions.
- Prefer trimming the middle of a long scene over deleting it entirely.
- End on a warm or funny moment, not an abrupt stop.

Target final duration: ~{target_minutes} minutes.

OUTPUT: Return ONLY a JSON object listing the regions to CUT. See user message for format."""

    else:
        return f"""You are an expert YouTube video editor.

Your job: identify the REGIONS TO CUT from a video transcript.
Everything not listed as a cut is kept.

RETENTION RULES (MrBeast Formula):
1. First 60s: MAXIMUM retention. Match title expectations.
2. Minutes 1-3: Transition hype to execution. Quick progression.
3. Re-engagement at ~30%: Spectacular moment or twist.
4. Back half: Allow slightly longer shots. Never signal the end.
5. Ending: Abrupt cut after clear payoff. No long outros.

CUT CRITERIA:
- Repetition, filler, weak takes, dead air
- Low-energy segments that don't advance the narrative
- Anything that makes the viewer want to click away

Target final duration: ~{target_minutes} minutes.

OUTPUT: Return ONLY a JSON object listing the regions to CUT. See user message for format."""


def _build_user_prompt(compact_segments, target_minutes, structure, captions,
                       total_src_dur=0.0, allow_zoom=False,
                       broll_enabled=False) -> str:
    """Build the user prompt with segment data."""
    extra_fields = ""
    extra_rules = ""

    if allow_zoom:
        extra_fields += """,
  "zooms": [
    {"start": 95.0, "end": 180.0, "scale": 1.1, "reason": "Emphasize the key claim"}
  ]"""
        extra_rules += """
- "zooms" (optional): 2-6 punch-in moments for emphasis (key claims, punchlines,
  emotional beats). scale between 1.05 and 1.15. Never two zooms back-to-back."""

    if broll_enabled:
        extra_fields += """,
  "broll": [
    {"start": 250.0, "end": 256.0, "query": "charts stock market"}
  ]"""
        extra_rules += """
- "broll" (optional): 2-8 moments where cutaway footage would help (lists,
  numbers, descriptions of places/things). 3-8 seconds each. query = 2-4
  keywords describing the footage needed. Never during emotional moments."""

    return f"""Analyze this {total_src_dur/60:.1f}-minute recording and identify regions to CUT.
Target final duration: ~{target_minutes} minutes (cut ~{max(0, total_src_dur/60 - target_minutes):.0f} min).
Structure: {', '.join(structure)}

TRANSCRIPT SEGMENTS:
{json.dumps(compact_segments, indent=1)}

Return ONLY valid JSON (no explanation, no markdown, no code fences):
{{
  "cuts": [
    {{"cut_start": 120.5, "cut_end": 135.2, "reason": "Dead air + false start"}},
    {{"cut_start": 340.0, "cut_end": 355.8, "reason": "Filler words, repeated take"}}
  ]{extra_fields}
}}

Rules:
- List ONLY the parts to REMOVE in "cuts". Everything else is kept automatically.
- Use exact timestamps from the segments above.
- Merge adjacent cuts into one range when possible.
- Each cut needs cut_start, cut_end (seconds), and reason.
- Do NOT cut from objection handling or closing sections.
- Cuts should total roughly {max(0, total_src_dur/60 - target_minutes):.0f} minutes.{extra_rules}"""


# ─── Cut-to-Keep Conversion ─────────────────────────────────────────

def _cuts_to_edl(cuts_json: dict, segments: List[Dict], total_dur: float,
                 captions: str, zoom_max: float = 1.15) -> dict:
    """
    Convert a list of cut ranges into keep_segments by finding the gaps.
    Everything NOT in a cut range becomes a keep segment.
    Zoom suggestions are mapped onto the keeps they overlap; B-roll
    suggestions pass through (the Resolve driver places them).
    """
    cuts = sorted(cuts_json.get("cuts", []), key=lambda c: c.get("cut_start", 0))

    # Build keep ranges from gaps between cuts
    keep_ranges = []
    cursor = 0.0

    for cut in cuts:
        cut_start = cut.get("cut_start", 0)
        cut_end = cut.get("cut_end", 0)
        if cut_start <= cursor:
            # Overlapping or adjacent cut -- just advance cursor
            cursor = max(cursor, cut_end)
            continue
        if cut_start > cursor:
            # Gap before this cut = keep range
            keep_ranges.append((cursor, cut_start))
        cursor = cut_end

    # Final keep range after last cut
    if cursor < total_dur:
        keep_ranges.append((cursor, total_dur))

    # Build keep_segments with segment index lookup
    keep_segments = []
    for i, (ks, ke) in enumerate(keep_ranges):
        # Find matching source segment for context
        reason = "Kept content"
        for seg in segments:
            if seg["start"] >= ks and seg["start"] < ke:
                reason = seg.get("transcript_excerpt", "")[:80]
                break

        # Map zoom suggestions: a zoom whose midpoint falls inside this
        # keep range sets the punch-in on the whole timeline item.
        zoom_val = None
        for z in cuts_json.get("zooms", []) or []:
            mid = (z.get("start", 0) + z.get("end", 0)) / 2
            if ks <= mid < ke:
                scale = float(z.get("scale", 1.1))
                zoom_val = round(min(max(scale, 1.0), zoom_max), 3)
                break

        keep_segments.append({
            "idx": i,
            "src_file": "synced_video.mp4",
            "keep_start": round(ks, 2),
            "keep_end": round(ke, 2),
            "reason": reason,
            "zoom": zoom_val,
            "jumpcut": False,
            "caption_text": None
        })

    # Build cut_segments list (segment indices that fall within cut ranges)
    cut_indices = []
    for seg in segments:
        seg_mid = (seg["start"] + seg["end"]) / 2
        for cut in cuts:
            if cut.get("cut_start", 0) <= seg_mid <= cut.get("cut_end", 0):
                cut_indices.append(seg["idx"])
                break

    # B-roll passes through in source time; the driver remaps to timeline
    broll = []
    for b in cuts_json.get("broll", []) or []:
        if b.get("query") and b.get("end", 0) > b.get("start", 0):
            broll.append({
                "start": round(float(b["start"]), 2),
                "end": round(float(b["end"]), 2),
                "query": str(b["query"])[:80],
            })

    return {
        "title": "Auto Edit",
        "keep_segments": keep_segments,
        "cut_segments": cut_indices,
        "music": {"track": None, "duck_to_db": -20},
        "loudness": {"target_lufs": -14},
        "captions_style": captions,
        "hook": [],
        "highlights": [],
        "broll": broll
    }


# ─── Debug Logging ──────────────────────────────────────────────────

def _debug_log_response(raw_response: str, cfg: dict):
    """Save raw LLM response to a debug file for troubleshooting."""
    import os
    out_dir = cfg.get("paths", {}).get("outputs_dir", "outputs")
    debug_path = os.path.join(out_dir, "_llm_response_debug.txt")
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(f"Response length: {len(raw_response)} chars\n")
            f.write("=" * 60 + "\n")
            f.write(raw_response)
        print(f"  (Debug: raw LLM response saved to {debug_path})")
    except Exception:
        pass  # Don't fail the pipeline over debug logging


# ─── Response Parsing ────────────────────────────────────────────────

def _parse_json_response(text: str) -> Optional[dict]:
    """Try to extract valid JSON from LLM response."""
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting JSON block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


# ─── Heuristic Fallback ─────────────────────────────────────────────

def _fallback_edl(segments: List[Dict], target_minutes: int, captions: str) -> dict:
    """
    Heuristic fallback if LLM fails.
    Keeps segments by energy score until target duration is met.
    """
    target_seconds = target_minutes * 60
    sorted_segs = sorted(segments, key=lambda s: s.get("energy", 0), reverse=True)

    keep = []
    total = 0.0
    for seg in sorted_segs:
        dur = seg["end"] - seg["start"]
        if total + dur <= target_seconds:
            keep.append({
                "idx": seg["idx"],
                "src_file": seg.get("src_file", "video.mp4"),
                "keep_start": seg["start"],
                "keep_end": seg["end"],
                "reason": f"High energy ({seg.get('energy', 0):.2f})",
                "zoom": None,
                "jumpcut": False,
                "caption_text": None
            })
            total += dur

    keep.sort(key=lambda s: s["keep_start"])

    return {
        "title": "Auto Edit (Heuristic Fallback)",
        "keep_segments": keep,
        "cut_segments": [],
        "music": {"track": None, "duck_to_db": -20},
        "loudness": {"target_lufs": -14},
        "captions_style": captions,
        "hook": [],
        "highlights": []
    }

