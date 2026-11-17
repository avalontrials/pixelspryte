"""fal.ai generation client — images, video, and ElevenLabs speech.

The client is deliberately small and dependency-free (standard library only) so
the same code runs on a fresh Windows box. It handles the three things that
matter for a reel pipeline:

* **reference inputs** — local images/audio are uploaded to fal's CDN when an
  endpoint wants HTTPS URLs, or inlined as data URIs when the endpoint accepts
  them (Kontext, MiniMax H3, Wan);
* **queue semantics** — the response's own ``status_url`` / ``response_url`` are
  used, because fal's short queue paths do not always contain the full endpoint
  id;
* **honest money** — every submission is estimated before it is sent and the
  ledger refuses to cross the brief's budget.

Auth: ``FAL_KEY`` from the environment or from ``PixelSpryte/.env``. A key is
never written to a plan, a manifest, or a log.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE = "https://queue.fal.run"
REST = "https://rest.fal.ai"
CDN = "https://v3.fal.media"
UPLOAD_CACHE = os.path.join(HERE, ".fal_uploads.json")

# Conservative planning rates. Verify at https://fal.ai/pricing before quoting.
# `unit` is how the rate is applied; the estimate is intentionally rounded up.
MODELS: dict[str, dict] = {
    "fal-ai/flux/schnell": {"kind": "image", "unit": "image", "usd": 0.003},
    "fal-ai/flux/dev": {"kind": "image", "unit": "megapixel", "usd": 0.025},
    "fal-ai/flux-pro/kontext": {"kind": "image", "unit": "image", "usd": 0.04},
    "elevenlabs/tts/eleven-v4": {"kind": "audio", "unit": "1000_chars", "usd": 0.08},
    "alibaba/wan-3.0/image-to-video": {"kind": "video", "unit": "second", "usd": 0.05},
    "minimax/h3-max/reference-to-video": {"kind": "video", "unit": "second", "usd": 0.05},
    "bytedance/seedance-2.5/text-to-video": {"kind": "video", "unit": "seedance_second", "usd": 0.473},
    "bytedance/seedance-2.5/image-to-video": {"kind": "video", "unit": "seedance_second", "usd": 0.473},
    "bytedance/seedance-2.5/reference-to-video": {"kind": "video", "unit": "seedance_second", "usd": 0.473},
    "fal-ai/kling-video/v1.6/standard/image-to-video": {"kind": "video", "unit": "second", "usd": 0.056},
    "fal-ai/kling-video/v1.6/pro/image-to-video": {"kind": "video", "unit": "second", "usd": 0.112},
    "fal-ai/kling-video/lipsync/audio-to-video": {"kind": "video", "unit": "request", "usd": 0.20},
    "veed/lipsync/v2": {"kind": "video", "unit": "request", "usd": 0.60},
}

# Seedance 2.5 is token-billed; these are conservative per-second planning
# equivalents at 16:9. Vertical output usually bills slightly lower.
SEEDANCE_PER_SECOND = {480: 0.2205, 720: 0.4730, 1080: 0.9500}

# Endpoints that need real HTTPS URLs rather than data URIs.
UPLOAD_HOSTS = {
    "bytedance/seedance-2.5/image-to-video",
    "bytedance/seedance-2.5/reference-to-video",
    "fal-ai/kling-video/lipsync/audio-to-video",
    "veed/lipsync/v2",
}

PRESET_SIZES = {
    "square_hd": (1024, 1024),
    "square": (512, 512),
    "portrait_4_3": (864, 1152),
    "portrait_16_9": (720, 1280),
    "landscape_4_3": (1152, 864),
    "landscape_16_9": (1280, 720),
}


class FalError(RuntimeError):
    pass


class BudgetError(FalError):
    pass


def _key() -> str | None:
    key = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY")
    if key:
        return key.strip()
    env_path = os.path.join(HERE, ".env")
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line.startswith("FAL_KEY") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def have_key() -> bool:
    return bool(_key())


def _request(url: str, payload: dict | None = None, method: str = "POST",
             timeout: int = 180, raw: bool = False) -> dict:
    key = _key()
    if not key:
        raise FalError(
            "No fal.ai key. Put FAL_KEY=... in PixelSpryte/.env (or the environment)."
        )
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Key {key}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:1200]
        raise FalError(f"fal HTTP {e.code}: {detail}") from None
    if raw:
        return {"text": text}
    return json.loads(text) if text else {}


def submit(model: str, payload: dict) -> dict:
    """Queue a job. Returns the queue response (request_id + the real URLs)."""
    data = _request(f"{QUEUE}/{model}", payload)
    if not data.get("request_id"):
        raise FalError(f"no request_id in fal response: {data}")
    return data


def wait(queued: dict, timeout: int = 1800, poll: float = 3.0) -> dict:
    """Poll the queue's own status URL, then read the result."""
    status_url = queued.get("status_url")
    response_url = queued.get("response_url")
    if not status_url or not response_url:
        raise FalError(f"queue response lacks status/response URLs: {queued}")
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        st = _request(status_url, method="GET", timeout=60)
        last = st.get("status", "")
        if last == "COMPLETED":
            return _request(response_url, method="GET", timeout=180)
        if last in ("FAILED", "CANCELLED"):
            raise FalError(f"fal job {queued.get('request_id')} {last}: {st.get('error')}")
        time.sleep(poll)
    raise FalError(f"fal job {queued.get('request_id')} timed out (last status: {last})")


def _first_url(result: dict) -> str:
    for key in ("images", "video", "videos", "audio", "output"):
        seq = result.get(key)
        if isinstance(seq, list) and seq:
            item = seq[0]
            if isinstance(item, dict):
                for k in ("url", "video_url"):
                    if k in item:
                        return item[k]
            if isinstance(item, str):
                return item
        if isinstance(seq, dict) and "url" in seq:
            return seq["url"]
    raise FalError(f"no output url in fal result keys={list(result)}")


def _download(url: str, out_path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=300) as r, open(out_path, "wb") as f:
            f.write(r.read())
    except Exception:
        # fal's media CDN sometimes lacks a chain the stdlib CA store trusts.
        # Windows' curl uses the OS trust store.
        curl = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe")
        if not os.path.exists(curl):
            raise
        subprocess.run([curl, "--fail", "--location", "--silent", "--show-error",
                        "--output", out_path, url], check=True, timeout=600)
    if os.path.getsize(out_path) < 1000:
        raise FalError(f"downloaded file suspiciously small: {out_path}")
    return out_path


def data_uri(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")


# ------------------------------------------------------------------ uploads
def _upload_token() -> dict:
    key = _key()
    if not key:
        raise FalError("No fal.ai key")
    req = urllib.request.Request(
        f"{REST}/storage/auth/token?storage_type=fal-cdn-v3",
        data=b"{}", method="POST",
        headers={"Authorization": f"Key {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _load_upload_cache() -> dict:
    if os.path.exists(UPLOAD_CACHE):
        try:
            return json.load(open(UPLOAD_CACHE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_upload_cache(cache: dict) -> None:
    with open(UPLOAD_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def upload(path: str) -> str:
    """Upload a local file to fal's CDN and return its HTTPS URL."""
    path = os.path.abspath(path)
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    cache = _load_upload_cache()
    if digest in cache and cache[digest].get("url"):
        return cache[digest]["url"]

    token = _upload_token()
    name = os.path.basename(path)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    req = urllib.request.Request(
        f"{CDN}/files/upload",
        data=open(path, "rb").read(),
        method="POST",
        headers={
            "Authorization": f"{token['token_type']} {token['token']}",
            "Content-Type": mime,
            "X-Fal-File-Name": name,
        },
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read().decode("utf-8"))
    url = data.get("access_url") or data.get("url")
    if not url:
        raise FalError(f"upload returned no access_url: {data}")
    cache[digest] = {"url": url, "name": name, "size": os.path.getsize(path)}
    _save_upload_cache(cache)
    return url


# ------------------------------------------------------------------ pricing
def _megapixels(size) -> float:
    if isinstance(size, dict):
        w, h = int(size.get("width", 1024)), int(size.get("height", 1024))
    elif isinstance(size, str) and size in PRESET_SIZES:
        w, h = PRESET_SIZES[size]
    else:
        w, h = 1080, 1920
    return (w * h) / 1_000_000


def estimate(spec: dict, source_size: tuple[int, int] | None = None) -> float:
    model = spec["model"]
    info = MODELS.get(model)
    if not info:
        return 0.0
    if model == "fal-ai/flux/dev":
        return round(info["usd"] * _megapixels(spec.get("size")), 4)
    if model == "elevenlabs/tts/eleven-v4":
        chars = len(spec.get("text", ""))
        return round(max(0.01, chars / 1000 * info["usd"]), 4)
    if info["unit"] == "seedance_second":
        res = str(spec.get("resolution", "720p")).replace("p", "")
        rate = SEEDANCE_PER_SECOND.get(int(res), SEEDANCE_PER_SECOND[720])
        return round(rate * float(spec.get("duration", 5)), 4)
    if info["unit"] == "second":
        return round(info["usd"] * float(spec.get("duration", 5)), 4)
    if info["unit"] == "request":
        return round(info["usd"], 4)
    if info["unit"] == "image":
        return round(info["usd"] * int(spec.get("num_images", 1)), 4)
    if info["unit"] == "megapixel":
        return round(info["usd"] * _megapixels(spec.get("size")), 4)
    return 0.0


# ------------------------------------------------------------------ payloads
def _as_url(value, model: str, allow_missing: bool = False) -> str:
    """Turn a local path into whatever the endpoint accepts (URL or data URI)."""
    if not value:
        raise FalError("empty media reference")
    if str(value).startswith(("http://", "https://", "data:")):
        return str(value)
    path = str(value)
    if allow_missing and not os.path.exists(path):
        return "file:///" + os.path.abspath(path).replace("\\", "/")
    if model in UPLOAD_HOSTS:
        return upload(path)
    return data_uri(path)


def _clean_payload(payload: dict) -> dict:
    """Drop planning-only keys before sending."""
    return {k: v for k, v in payload.items() if v is not None}


def build_payload(spec: dict, generated: dict[str, str] | None = None,
                  dry_run: bool = False) -> dict:
    """Build the endpoint payload. ``generated`` maps ids to local output paths."""
    generated = generated or {}
    model = spec["model"]

    def media_url(value):
        return _as_url(value, model, allow_missing=dry_run)
    kind = spec.get("kind", MODELS.get(model, {}).get("kind", "image"))

    def ref(value):
        return generated.get(value, value) if isinstance(value, str) else value

    def media(key: str):
        if spec.get(key):
            return ref(spec[key])
        gen = spec.get(f"{key}_generated")
        return generated.get(gen) if gen else None

    if model == "elevenlabs/tts/eleven-v4":
        return _clean_payload({
            "text": spec["text"],
            "voice": spec.get("voice", "Chris"),
            "stability": spec.get("stability", 0.5),
            "similarity_boost": spec.get("similarity_boost", 0.75),
            "language_code": spec.get("language_code", "en"),
            "apply_text_normalization": spec.get("apply_text_normalization", "auto"),
            "timestamps": spec.get("timestamps", True),
            "output_format": spec.get("output_format", "mp3_44100_192"),
            "seed": spec.get("seed"),
            "enable_safety_checker": spec.get("enable_safety_checker", True),
        })

    if model == "fal-ai/flux/dev" or model == "fal-ai/flux/schnell":
        return _clean_payload({
            "prompt": spec["prompt"],
            "image_size": spec.get("size", "portrait_16_9"),
            "num_images": spec.get("num_images", 1),
            "enable_safety_checker": spec.get("safety_checker", False),
            "seed": spec.get("seed"),
        })

    if model == "fal-ai/flux-pro/kontext":
        img = media("source")
        return _clean_payload({
            "prompt": spec["prompt"],
            "image_url": media_url(img),
            "aspect_ratio": spec.get("aspect_ratio", "9:16"),
            "num_images": spec.get("num_images", 1),
            "output_format": spec.get("output_format", "jpeg"),
            "safety_tolerance": str(spec.get("safety_tolerance", "2")),
            "seed": spec.get("seed"),
        })

    if model == "bytedance/seedance-2.5/text-to-video":
        return _clean_payload({
            "prompt": spec.get("prompt", ""),
            "duration": str(int(spec.get("duration", 5))),
            "resolution": spec.get("resolution", "720p"),
            "aspect_ratio": spec.get("aspect_ratio", "9:16"),
            "generate_audio": bool(spec.get("generate_audio", False)),
            "seed": spec.get("seed"),
        })

    if model in ("bytedance/seedance-2.5/reference-to-video",
                 "bytedance/seedance-2.5/image-to-video"):
        payload = {
            "prompt": spec.get("prompt", ""),
            "duration": str(int(spec.get("duration", 5))),
            "resolution": spec.get("resolution", "720p"),
            "generate_audio": bool(spec.get("generate_audio", False)),
            "seed": spec.get("seed"),
        }
        if model.endswith("reference-to-video"):
            refs = [ref(p) for p in (spec.get("reference_images") or [])]
            payload["image_urls"] = [media_url(p) for p in refs]
            payload["aspect_ratio"] = spec.get("aspect_ratio", "9:16")
            payload["task"] = spec.get("task", "reference")
        else:
            payload["image_url"] = media_url(media("source"))
        return _clean_payload(payload)

    if model == "minimax/h3-max/reference-to-video":
        img = media("source")
        refs = [ref(p) for p in (spec.get("reference_images") or [])]
        audios = spec.get("reference_audio") or []
        if isinstance(audios, str):
            audios = [audios]
        audios = [ref(p) for p in audios]
        return _clean_payload({
            "prompt": spec.get("prompt", ""),
            "duration": float(spec.get("duration", 6)),
            "resolution": spec.get("resolution", "768P"),
            "aspect_ratio": spec.get("aspect_ratio", "9:16"),
            "prompt_expansion_mode": spec.get("prompt_expansion_mode", "disabled"),
            "enable_safety_checker": spec.get("enable_safety_checker", True),
            "image_url": media_url(img) if img else None,
            "reference_image_urls": [media_url(p) for p in refs] or None,
            "reference_audio_urls": [media_url(p) for p in audios] or None,
            "seed": spec.get("seed"),
        })

    if model == "veed/lipsync/v2":
        video = ref(spec.get("video")) or media("source")
        audio = ref(spec.get("audio")) or (spec.get("reference_audio") or [None])[0]
        audio = ref(audio)
        return _clean_payload({
            "video_url": media_url(video),
            "audio_url": media_url(audio),
        })

    if model == "fal-ai/kling-video/lipsync/audio-to-video":
        video = ref(spec.get("video")) or media("source")
        audio = ref(spec.get("audio")) or (spec.get("reference_audio") or [None])[0]
        audio = ref(audio)
        return _clean_payload({
            "video_url": media_url(video),
            "audio_url": media_url(audio),
            "enable_safety_checker": spec.get("enable_safety_checker", True),
        })

    if model.startswith("fal-ai/kling-video/v1.6/"):
        img = media("source")
        return _clean_payload({
            "prompt": spec.get("prompt", ""),
            "image_url": media_url(img) if img else None,
            "duration": str(int(spec.get("duration", 5))),
            "negative_prompt": spec.get("negative_prompt", "blur, distort, and low quality"),
            "cfg_scale": spec.get("cfg_scale", 0.5),
        })

    if model == "alibaba/wan-3.0/image-to-video":
        img = media("source")
        return _clean_payload({
            "prompt": spec.get("prompt", ""),
            "start_image_url": media_url(img) if img else None,
            "resolution": spec.get("resolution", "720p"),
            "aspect_ratio": spec.get("aspect_ratio", "9:16"),
            "duration": int(spec.get("duration", 6)),
            "audio": bool(spec.get("audio", False)),
            "enable_prompt_expansion": spec.get("enable_prompt_expansion", False),
            "enable_thinking": spec.get("enable_thinking", False),
            "seed": spec.get("seed"),
            "enable_safety_checker": spec.get("enable_safety_checker", True),
        })

    raise FalError(f"unsupported model: {model}")


# ------------------------------------------------------------------ jobs
def generate(spec: dict, out_dir: str, generated: dict[str, str] | None = None,
             dry_run: bool = False) -> dict:
    """Run one generation spec and return a ledger record.

    spec: {id, model, kind, prompt/text, ...}
    """
    model = spec["model"]
    kind = spec.get("kind", MODELS.get(model, {}).get("kind", "image"))
    ext = {"video": "mp4", "audio": "mp3", "image": "png"}[kind]
    out_path = os.path.join(out_dir, f"{spec['id']}.{ext}")
    est = estimate(spec)

    record = {
        "id": spec["id"],
        "model": model,
        "kind": kind,
        "path": out_path,
        "est_usd": est,
        "dry_run": dry_run,
    }
    if spec.get("reuse") and os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
        record.update({"reused": True, "bytes": os.path.getsize(out_path)})
        sidecar = os.path.splitext(out_path)[0] + ".timestamps.json"
        if kind == "audio" and os.path.exists(sidecar):
            with open(sidecar, encoding="utf-8") as f:
                stamps = json.load(f)
            record["timestamps"] = sidecar
            record["words"] = words_from_timestamps(stamps)
        return record
    payload = build_payload(spec, generated, dry_run=dry_run)
    if dry_run:
        record["payload"] = _redact_payload(payload)
        return record

    queued = submit(model, payload)
    result = wait(queued)
    _download(_first_url(result), out_path)
    record.update({
        "request_id": queued["request_id"],
        "bytes": os.path.getsize(out_path),
        "result": {k: v for k, v in result.items() if k != "timestamps"},
    })
    if kind == "audio" and result.get("timestamps"):
        sidecar = os.path.splitext(out_path)[0] + ".timestamps.json"
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump(result["timestamps"], f, indent=2)
        record["timestamps"] = sidecar
        record["words"] = words_from_timestamps(result["timestamps"])
    return record


def _redact_payload(payload: dict) -> dict:
    """Never write megabyte data URIs or signed upload URLs into a plan."""
    clean = {}
    for k, v in payload.items():
        if isinstance(v, str) and (v.startswith("data:") or "fal.media" in v):
            clean[k] = "<media>"
        elif isinstance(v, list):
            clean[k] = ["<media>" if isinstance(x, str) and (x.startswith("data:") or "fal.media" in x) else x
                        for x in v]
        else:
            clean[k] = v
    return clean


def generate_all(specs: list[dict], out_dir: str, dry_run: bool = False,
                 budget_limit: float | None = None) -> list[dict]:
    """Run a dependency-ordered shot list, enforcing the budget before each POST."""
    results: list[dict] = []
    generated: dict[str, str] = {}
    spent = 0.0
    for spec in specs:
        est = estimate(spec)
        if budget_limit is not None and spent + est > budget_limit + 1e-9:
            raise BudgetError(
                f"fal budget would be exceeded: ${spent:.2f} spent + ${est:.2f} for "
                f"'{spec.get('id')}' > ${budget_limit:.2f}"
            )
        r = generate(spec, out_dir, generated=generated, dry_run=dry_run)
        results.append(r)
        generated[r["id"]] = r["path"]
        spent += est
        print(f"  fal: {r['id']} <- {r['model']} (~${r['est_usd']:.3f})"
              + (" [dry-run]" if dry_run else f" -> {os.path.getsize(r['path'])/1e6:.2f} MB"))
    return results


# ------------------------------------------------------------------ timestamps
def words_from_timestamps(timestamps: list[dict]) -> list[dict]:
    """Turn ElevenLabs character timings into word timings.

    The API returns chunks of character/start/end arrays; a word can span a
    chunk boundary, so characters are walked in order and grouped on whitespace.
    """
    words: list[dict] = []
    cur, start, end = "", None, None
    for chunk in timestamps or []:
        chars = chunk.get("characters") or []
        starts = chunk.get("character_start_times_seconds") or []
        ends = chunk.get("character_end_times_seconds") or []
        for i, ch in enumerate(chars):
            if ch.isspace():
                if cur:
                    words.append({"word": cur, "start": round(start, 3), "end": round(end, 3)})
                    cur, start, end = "", None, None
                continue
            if start is None:
                start = starts[i] if i < len(starts) else (words[-1]["end"] if words else 0.0)
            cur += ch
            if i < len(ends):
                end = ends[i]
    if cur:
        words.append({"word": cur, "start": round(start, 3), "end": round(end or start, 3)})
    return words
