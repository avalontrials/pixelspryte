"""Captions: cues -> SRT, plus a simple word-timing helper for Whisper output."""
from __future__ import annotations

import os


def _ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: list[dict], path: str) -> str:
    """cues: [{"start": float, "end": float, "text": str}]"""
    clean = [c for c in cues if c.get("text", "").strip() and c["end"] > c["start"]]
    clean.sort(key=lambda c: c["start"])
    lines = []
    for i, c in enumerate(clean, start=1):
        text = " ".join(c["text"].split())
        lines += [str(i), f"{_ts(c['start'])} --> {_ts(c['end'])}", text, ""]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")
    return path


def offset_words(words: list[dict], offset: float) -> list[dict]:
    return [{"word": w["word"], "start": round(w["start"] + offset, 3),
             "end": round(w["end"] + offset, 3)} for w in words]


def cues_for_line(words: list[dict], offset: float = 0.0,
                  max_chars: int = 34, max_gap: float = 0.60) -> list[dict]:
    """Build readable reel captions for one spoken line."""
    return chunks_from_words(offset_words(words, offset), max_chars=max_chars, max_gap=max_gap)


def chunks_from_words(words: list[dict], max_chars: int = 42, max_gap: float = 0.45) -> list[dict]:
    """Group word timings into cue-sized chunks.

    words: [{"start": float, "end": float, "word": str}] or {"text": ...}
    """
    cues, cur, cur_start, last_end = [], [], None, None
    for w in words:
        token = w.get("word") or w.get("text") or ""
        token = token.strip()
        if not token:
            continue
        if cur_start is None:
            cur_start = w["start"]
        joined = " ".join(cur + [token])
        gap = (w["start"] - last_end) if last_end is not None else 0.0
        if len(joined) > max_chars or gap > max_gap or token.endswith((".", "?", "!")):
            if cur:
                cues.append({"start": cur_start, "end": last_end, "text": " ".join(cur)})
            cur, cur_start = [token], w["start"]
        else:
            cur.append(token)
        last_end = w["end"]
    if cur:
        cues.append({"start": cur_start, "end": last_end, "text": " ".join(cur)})
    return cues
