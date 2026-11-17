#!/usr/bin/env python3
"""Build one reel: brief JSON -> generated assets + overlays + audio -> Resolve.

Usage:
    py -3.12 build_reel.py reels/demo/brief.json
    py -3.12 build_reel.py reels/demo/brief.json --generate-only
    py -3.12 build_reel.py reels/demo/brief.json --fal-dry-run

The original demo brief still works: it has no `generate`, no `broll`, and no
`audio` block, so the legacy path (mirror footage audio) is used.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from ps import audio as ps_audio  # noqa: E402
from ps import broll as ps_broll  # noqa: E402
from ps import captions, falgen, graphics, otio_build, overlay, review, verify  # noqa: E402
from ps.resolve import Resolve  # noqa: E402


def log(msg: str) -> None:
    print(msg, flush=True)


def _resolve_local(p: str, base: str) -> str:
    if os.path.isabs(p):
        return os.path.normpath(p)
    for root in (base, HERE):
        cand = os.path.join(root, p)
        if os.path.exists(cand):
            return os.path.normpath(cand)
    raise FileNotFoundError(f"file not found: {p}")


def load_brief(path: str) -> tuple[dict, str]:
    with open(path, encoding="utf-8") as f:
        brief = json.load(f)
    base = os.path.dirname(os.path.abspath(path))

    def fix(c: dict) -> None:
        if c.get("path"):
            c["path"] = _resolve_local(c["path"], base)

    for c in brief.get("footage", []):
        fix(c)
    for layer in brief.get("video_layers", []):
        for c in layer.get("clips", []):
            fix(c)
    for c in brief.get("overlays", []):
        fix(c)
    for a in brief.get("audio_tracks", []):
        for c in a.get("clips", []):
            fix(c)
    if isinstance(brief.get("audio"), list):
        for c in brief["audio"]:
            fix(c)
    for ch in brief.get("characters", []):
        if ch.get("reference"):
            ch["reference"] = _resolve_local(ch["reference"], base)
    return brief, base


def _materialize(clip: dict, generated: dict[str, str], broll: dict[str, dict]) -> None:
    if clip.get("generated"):
        gid = clip["generated"]
        if gid not in generated:
            raise KeyError(f"generated asset not found: {gid}")
        clip["path"] = generated[gid]
    if clip.get("broll"):
        bid = clip["broll"]
        if bid not in broll:
            raise KeyError(f"b-roll not found: {bid}")
        clip["path"] = broll[bid]["path"]


def prepare_broll(brief: dict) -> dict[str, dict]:
    """Fetch declared b-roll and return id -> entry."""
    entries: dict[str, dict] = {}
    for item in brief.get("broll", []):
        cid = item.get("id")
        if not cid:
            raise ValueError("b-roll entry needs an id")
        if item.get("path"):
            path = _resolve_local(item["path"], HERE)
            entry = {"id": cid, "path": path, **item}
        else:
            entry = ps_broll.fetch(cid)
            entry.update({k: v for k, v in item.items() if k not in entry})
        entries[cid] = entry
    return entries


def _duration(path: str) -> float:
    return ps_audio.duration(path)


def build_narration(brief: dict, generated: dict[str, str]) -> list[dict]:
    """Resolve narration entries to {path, at, in, out, duration, words}."""
    spec = brief.get("audio") or {}
    entries = spec.get("narration") if isinstance(spec, dict) else None
    if entries is None and isinstance(brief.get("audio"), list):
        entries = brief["audio"]
    if not entries:
        return []

    out, cursor = [], 0.0
    for i, e in enumerate(entries):
        if e.get("generated"):
            path = generated.get(e["generated"])
            if not path:
                raise KeyError(f"narration generated asset not found: {e['generated']}")
            words = e.get("words") or (e.get("_words"))
        else:
            path = e.get("path")
        if not path or not os.path.exists(path):
            raise FileNotFoundError(f"narration audio missing: {path}")
        dur = _duration(path)
        at = e.get("at")
        if at is None:
            at = cursor + float(e.get("gap", 0.0))
        at = round(float(at), 3)
        out.append({
            "path": path,
            "at": at,
            "in": float(e.get("in", 0.0)),
            "out": float(e.get("out", dur)),
            "duration": dur,
            "words": e.get("words"),
            "voice": e.get("voice"),
            "text": e.get("text", ""),
        })
        cursor = at + dur
    return out


def _overlay_cards(brief: dict, out_dir: str, canvas: tuple[int, int],
                   generated: dict[str, str], slug: str) -> list[dict]:
    cards = []
    for i, ov in enumerate(brief.get("overlays", [])):
        if ov.get("path"):
            cards.append({**ov})
            continue
        if ov.get("generated"):
            cards.append({**ov, "path": generated[ov["generated"]]})
            continue
        gfx = ov.get("graphic")
        if gfx:
            p = os.path.join(out_dir, "overlays", f"gfx_{chr(97 + i % 26)}_{slug}.png")
            params = dict(ov.get("params") or {})
            fn = getattr(graphics, gfx, None)
            if not fn:
                raise ValueError(f"unknown graphic: {gfx}")
            fn(p, canvas, **params)
            cards.append({**ov, "path": p})
            log(f"  graphic {i}: {os.path.basename(p)}")
            continue
        card = ov.get("card", {})
        p = os.path.join(out_dir, "overlays", f"card_{chr(97 + i % 26)}_{slug}.png")
        overlay.card(
            p, canvas,
            headline=card.get("headline"),
            kicker=card.get("kicker"),
            subhead=card.get("subhead"),
            y_frac=card.get("y_frac", 0.10),
            align=card.get("align", "center"),
            headline_frac=card.get("headline_frac", 0.062),
        )
        cards.append({**ov, "path": p})
        log(f"  overlay {i}: {os.path.basename(p)}")
    return cards


def _caption_cards(cues: list[dict], out_dir: str, canvas: tuple[int, int],
                   slug: str) -> list[dict]:
    clips = []
    for i, cue in enumerate(cues):
        # Resolve's OTIO importer has tripped on numbered still names before.
        # Letter suffixes keep the names non-numeric and import reliably.
        tag = chr(97 + i) if i < 26 else f"a{chr(97 + i - 26)}"
        cpath = os.path.join(out_dir, "captions", f"cap_{tag}_{slug}.png")
        overlay.caption_card(cpath, canvas, cue["text"])
        clips.append({"path": cpath, "at": float(cue["start"]), "in": 0.0,
                      "out": max(0.4, float(cue["end"]) - float(cue["start"]))})
    return clips


def _cues_from_narration(narration: list[dict]) -> list[dict]:
    cues = []
    for n in narration:
        words = n.get("words")
        if not words:
            continue
        for c in captions.cues_for_line(words, offset=n["at"], max_chars=34, max_gap=0.55):
            c["text"] = c["text"].upper()
            cues.append(c)
    cues.sort(key=lambda c: c["start"])
    # Never let two cards overlap.
    for a, b in zip(cues, cues[1:]):
        if a["end"] > b["start"] - 0.02:
            a["end"] = max(a["start"] + 0.25, b["start"] - 0.02)
    return cues


def stage_media(paths: list[str], out_dir: str) -> dict[str, str]:
    media_dir = os.path.join(out_dir, "media")
    os.makedirs(media_dir, exist_ok=True)
    mapping: dict[str, str] = {}
    seen: dict[str, str] = {}
    for path in paths:
        path = os.path.normpath(os.path.abspath(path))
        if path in mapping:
            continue
        base = os.path.basename(path)
        if base in seen and seen[base] != path:
            stem, ext = os.path.splitext(base)
            base = f"{stem}_{len(seen)}{ext}"
        dst = os.path.normpath(os.path.join(media_dir, base))
        if not os.path.exists(dst):
            try:
                os.link(path, dst)
            except OSError:
                shutil.copy2(path, dst)
        seen[base] = path
        mapping[path] = dst
    return mapping


def _remap(obj, mapping: dict[str, str]) -> None:
    if isinstance(obj, dict):
        if "path" in obj:
            key = os.path.normpath(obj["path"])
            if key in mapping:
                obj["path"] = mapping[key]
        for v in obj.values():
            _remap(v, mapping)
    elif isinstance(obj, list):
        for v in obj:
            _remap(v, mapping)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("brief")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--keep-project", action="store_true")
    ap.add_argument("--preset", default=None)
    ap.add_argument("--fal-dry-run", action="store_true", help="plan fal jobs without calling the API")
    ap.add_argument("--no-fal", action="store_true", help="skip generation entirely")
    ap.add_argument("--generate-only", action="store_true", help="generate assets, then stop")
    args = ap.parse_args()

    brief, brief_dir = load_brief(args.brief)
    slug = brief.get("slug") or os.path.splitext(os.path.basename(args.brief))[0]
    canvas_spec = brief["canvas"]
    w, h, fps = int(canvas_spec["w"]), int(canvas_spec["h"]), int(canvas_spec["fps"])
    out_dir = os.path.join(HERE, "out", slug)
    os.makedirs(out_dir, exist_ok=True)
    canvas = (w, h)
    log(f"PixelSpryte — reel '{slug}'  {w}x{h}@{fps}")

    # 0. b-roll -------------------------------------------------------------
    broll_map = prepare_broll(brief)
    for cid, e in broll_map.items():
        log(f"  b-roll: {cid} {e.get('duration', '?')}s {e.get('width', '?')}x{e.get('height', '?')}")

    # 1. generated assets ---------------------------------------------------
    gen_specs = [] if args.no_fal else brief.get("generate", [])
    ledger: list[dict] = []
    budget = brief.get("budget") or {}
    limit = budget.get("limit_usd")
    if gen_specs:
        if not falgen.have_key() and not args.fal_dry_run:
            raise RuntimeError("No FAL_KEY found (.env or environment). Use --fal-dry-run to plan only.")
        ledger = falgen.generate_all(
            gen_specs, os.path.join(out_dir, "gen"),
            dry_run=args.fal_dry_run,
            budget_limit=float(limit) if limit else None,
        )
        by_id = {r["id"]: r for r in ledger}
        if args.fal_dry_run:
            plan = {
                "jobs": ledger,
                "total_est_usd": round(sum(r.get("est_usd", 0) for r in ledger), 4),
                "budget_limit_usd": limit,
            }
            with open(os.path.join(out_dir, "fal_plan.json"), "w", encoding="utf-8") as f:
                json.dump(plan, f, indent=2)
            log(f"  fal plan: {len(ledger)} jobs, ~${plan['total_est_usd']} -> fal_plan.json")
            return 0
        # Attach timestamp words to narration entries by generated id.
        for e in (brief.get("audio") or {}).get("narration", []):
            if e.get("generated") and by_id.get(e["generated"], {}).get("words"):
                e["words"] = by_id[e["generated"]]["words"]
        if args.generate_only:
            assets = {
                "generated": {r["id"]: r["path"] for r in ledger},
                "broll": {k: v["path"] for k, v in broll_map.items()},
                "jobs": ledger,
            }
            with open(os.path.join(out_dir, "assets.json"), "w", encoding="utf-8") as f:
                json.dump(assets, f, indent=2)
            log(f"  --generate-only: wrote {os.path.join(out_dir, 'assets.json')}")
            return 0
    else:
        by_id = {}
        if args.generate_only:
            log("  nothing to generate")
            return 0

    generated = {k: v["path"] for k, v in by_id.items()}

    # 2. materialize paths --------------------------------------------------
    for c in brief.get("footage", []):
        _materialize(c, generated, broll_map)
    for layer in brief.get("video_layers", []):
        for c in layer.get("clips", []):
            _materialize(c, generated, broll_map)
    for ov in brief.get("overlays", []):
        _materialize(ov, generated, broll_map)
    for n in (brief.get("audio") or {}).get("narration", []):
        if n.get("generated") and n["generated"] in generated:
            n["path"] = generated[n["generated"]]

    # 3. narration + audio mix ---------------------------------------------
    narration = build_narration(brief, generated)
    audio_cfg = brief.get("audio") or {}
    music = audio_cfg.get("music") if isinstance(audio_cfg, dict) else None
    if music and music.get("path"):
        music["path"] = _resolve_local(music["path"], brief_dir)

    # 4. overlays -----------------------------------------------------------
    cards = _overlay_cards(brief, out_dir, canvas, generated, slug)

    # 5. captions -----------------------------------------------------------
    cues = brief.get("captions") or []
    if not cues and (brief.get("captions_from_tts") or narration):
        cues = _cues_from_narration(narration)
        log(f"  captions: {len(cues)} cues derived from speech timing")
    burn = str(brief.get("captions_mode", "burn")).lower() == "burn"
    caption_clips = _caption_cards(cues, out_dir, canvas, slug) if (burn and cues) else []
    srt_path = os.path.join(out_dir, f"{slug}.srt")
    if cues:
        captions.write_srt(cues, srt_path)
        log(f"  captions: {len(cues)} cues -> {os.path.basename(srt_path)}")

    # 6. total duration and mix --------------------------------------------
    timeline_spec = {
        "name": brief.get("name", slug),
        "canvas": canvas_spec,
        "footage": brief.get("footage", []),
        "video_layers": brief.get("video_layers", []),
        "caption_layer": {"clips": caption_clips} if caption_clips else None,
        "overlays": cards,
    }
    total = otio_build.total_seconds({**timeline_spec, "audio_tracks": []})
    if not total:
        raise RuntimeError("brief has no footage")
    total = round(total, 3)
    log(f"  planned duration: {total:.2f}s")

    audio_tracks = []
    mix_path = None
    loud = {}
    if narration or music:
        mix_path = os.path.join(out_dir, "audio", f"{slug}_mix.wav")
        ps_audio.build_mix(
            [{"path": n["path"], "at": n["at"], "gain_db": n.get("gain_db", 0.0)} for n in narration],
            mix_path, total, music=music, lufs=float(audio_cfg.get("lufs", -14.0) if isinstance(audio_cfg, dict) else -14.0),
        )
        loud = ps_audio.loudness(mix_path)
        audio_tracks.append({"name": "A1-mix", "clips": [{"path": mix_path, "at": 0.0, "in": 0.0, "out": total}]})
        log(f"  audio mix: {os.path.basename(mix_path)}  {loud}")
    else:
        # Legacy briefs have no audio block: mirror the footage sequentially.
        legacy, cursor = [], 0.0
        for c in brief.get("footage", []):
            legacy.append({"path": c["path"], "at": round(cursor, 3),
                           "in": c.get("in", 0.0), "out": c.get("out")})
            cursor += float(c.get("out", 0.0)) - float(c.get("in", 0.0))
        audio_tracks = [{"name": "A1", "clips": legacy}]

    # 7. stage media beside the timeline (Resolve resolves OTIO paths relatively)
    paths = [c["path"] for c in timeline_spec["footage"]]
    for layer in timeline_spec["video_layers"]:
        paths += [c["path"] for c in layer.get("clips", [])]
    paths += [c["path"] for c in timeline_spec["overlays"]]
    paths += [c["path"] for c in (timeline_spec["caption_layer"] or {}).get("clips", [])]
    for a in audio_tracks:
        paths += [c["path"] for c in a["clips"]]
    mapping = stage_media(paths, out_dir)
    _remap(timeline_spec, mapping)
    _remap(audio_tracks, mapping)
    log(f"  staged {len(set(mapping.values()))} media files under {os.path.join(out_dir, 'media')}")

    # 8. timeline file ------------------------------------------------------
    otio_path = os.path.join(out_dir, f"{slug}.otio")
    otio_build.build({**timeline_spec, "audio_tracks": audio_tracks}, otio_path)
    expected = otio_build.total_seconds({**timeline_spec, "audio_tracks": audio_tracks})
    log(f"  otio: {os.path.basename(otio_path)}  ({expected:.2f}s)")

    # 9. resolve ------------------------------------------------------------
    started = time.time()
    rs = Resolve().connect()
    log(f"  resolve: {rs.version}")
    project = rs.project(f"PS_{slug}", width=w, height=h, fps=fps)
    try:
        timeline = project.import_timeline(otio_path, timeline_name=slug)
        v_tracks = timeline.GetTrackCount("video")
        a_tracks = timeline.GetTrackCount("audio")
        v1 = timeline.GetItemListInTrack("video", 1) or []
        span = timeline.GetEndFrame() - timeline.GetStartFrame() + 1
        log(f"  timeline: {len(v1)} clips on V1, {v_tracks} video / {a_tracks} audio tracks, {span} frames")
        if not v1:
            raise RuntimeError("timeline has no clips on V1")
        if abs(span - round(expected * fps)) > max(3, fps // 5):
            raise RuntimeError(f"timeline span {span} frames != expected ~{round(expected * fps)}")

        frame_tracks = [1] + list(range(2, 2 + len(timeline_spec["video_layers"])))
        framed = project.frame_fill_tracks(timeline, frame_tracks, mode="fill")
        for row in framed:
            log(f"  framing: {row}")
        top_tracks = list(range(2 + len(timeline_spec["video_layers"]), v_tracks + 1))
        for trk in top_tracks:
            for row in project.frame_fill(timeline, track=trk, mode="fit"):
                log(f"  overlay fit: {row}")

        # Keep only the deliberate mix audible. Linked source audio from B-roll
        # can otherwise leak in under the VO.
        if a_tracks > 1:
            for trk in range(2, a_tracks + 1):
                n = project.set_audio_volume(timeline, trk, -60.0)
                if n:
                    log(f"  muted {n} source-audio clips on A{trk}")

        if cues and not burn:
            n = project.append_srt(timeline, srt_path)
            log(f"  subtitles: {n} cues on subtitle track")

        if args.no_render:
            log("  --no-render: stopping before render")
            return 0

        preset = args.preset or brief.get("render", {}).get("preset", "TikTok - 1080p")
        basename = brief.get("render", {}).get("name", f"{slug}_v1")
        path, elapsed = project.render(out_dir, basename, preset=preset,
                                       width=w, height=h, fps=fps)
        log(f"  render: {os.path.getsize(path)/1e6:.2f} MB in {elapsed:.1f}s -> {path}")

        result = verify.check(path, {
            "width": w, "height": h, "fps": fps,
            "duration": expected, "min_frames": round(expected * fps) - 3,
            "has_audio": bool(narration or music),
        })
        fal_block = {
            "spend_usd": round(sum(r.get("est_usd", 0) for r in ledger if not r.get("reused")), 4),
            "budget_limit_usd": limit,
            "jobs": [{k: v for k, v in r.items() if k not in ("result", "words")} for r in ledger],
        }
        ledger_path = os.path.join(out_dir, "fal_ledger.json")
        if os.path.exists(ledger_path):
            try:
                with open(ledger_path, encoding="utf-8") as f:
                    session = json.load(f)
                fal_block.update({
                    "session_estimated_usd": session.get("total_estimated_usd"),
                    "session_conservative_usd": session.get("conservative_total_usd"),
                    "session_remaining_usd": session.get("remaining_under_limit_usd"),
                    "session_ledger": ledger_path,
                })
            except Exception:
                pass
        manifest = {
            "slug": slug,
            "brief": os.path.abspath(args.brief),
            "name": brief.get("name", slug),
            "canvas": {"w": w, "h": h, "fps": fps},
            "footage_seconds": round(expected, 3),
            "output": result["info"],
            "checks": {"ok": result["ok"], "problems": result["problems"]},
            "build_seconds": round(time.time() - started, 1),
            "fal": fal_block,
            "audio": {"mix": mix_path, **loud},
            "broll": list(broll_map.values()),
            "characters": brief.get("characters", []),
            "timeline": {"video_tracks": v_tracks, "audio_tracks": a_tracks,
                         "frames": span, "captions": len(cues)},
        }
        with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        # 10. review artifacts ---------------------------------------------
        if result["ok"]:
            try:
                frames = review.filmstrip(path, os.path.join(out_dir, "review"), count=8)
                sheet = review.contact_sheet(frames, os.path.join(out_dir, "review", "contact-sheet.jpg"))
                report = review.write_report(out_dir, manifest, frames, sheet, brief)
                log(f"  review: {report}")
            except Exception as e:
                log(f"  review skipped: {type(e).__name__}: {e}")

        if not result["ok"]:
            log("  VERIFY FAILED: " + "; ".join(result["problems"]))
            return 2
        log(f"  verified: {result['info']['width']}x{result['info']['height']} "
            f"{result['info']['vcodec']}/{result['info']['acodec']} "
            f"{result['info']['duration']}s {result['info']['frames']}f")
        log(f"  manifest: {os.path.join(out_dir, 'manifest.json')}")
        return 0
    finally:
        if not args.keep_project:
            project.close(delete=False)


if __name__ == "__main__":
    raise SystemExit(main())
