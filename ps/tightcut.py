"""Find the dead air in a recording and turn it into keep-ranges.

No model, no key: ffmpeg's silencedetect reads the audio level, we invert the
result, and the keep-ranges become footage clips in a brief. This is the first
half of "record yourself, let the machine cut it".
"""
from __future__ import annotations

import json
import re
import subprocess

SILENCE_RE = re.compile(r"silence_(start|end):\s*([0-9.]+)")


def duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, timeout=60,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        raise RuntimeError(f"Could not read the length of {path}")


def silences(path: str, noise_db: int = -32, min_silence: float = 0.55) -> list[tuple[float, float]]:
    """Return [(start, end), ...] for every pause longer than min_silence."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
         "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=3600,
    )
    text = proc.stderr or ""
    starts: list[float] = []
    ends: list[float] = []
    for kind, value in SILENCE_RE.findall(text):
        if kind == "start":
            starts.append(float(value))
        else:
            ends.append(float(value))
    # A silence that runs to the end of the file has no silence_end line.
    if len(starts) > len(ends):
        ends.append(duration(path))
    return list(zip(starts, ends))


def keep_ranges(path: str, noise_db: int = -32, min_silence: float = 0.55,
                breath: float = 0.18, min_keep: float = 0.4) -> list[tuple[float, float]]:
    """Invert the silences, leave a little breath, drop slivers.

    breath: seconds of air kept on each side of a cut, so the cut does not clip
    the first or last syllable. min_keep: anything shorter is a sneeze, not a take.
    """
    total = duration(path)
    quiet = silences(path, noise_db=noise_db, min_silence=min_silence)

    keeps: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in quiet:
        keep_start = cursor
        keep_end = max(keep_start, start + breath)
        if keep_end - keep_start >= min_keep:
            keeps.append((round(keep_start, 3), round(keep_end, 3)))
        cursor = max(cursor, end - breath)

    if total - cursor >= min_keep:
        keeps.append((round(cursor, 3), round(total, 3)))

    if not keeps:
        keeps = [(0.0, round(total, 3))]
    return keeps


def plan(path: str, **kwargs) -> dict:
    keeps = keep_ranges(path, **kwargs)
    total = duration(path)
    kept = sum(b - a for a, b in keeps)
    return {
        "source_seconds": round(total, 3),
        "kept_seconds": round(kept, 3),
        "cuts": len(keeps) - 1,
        "keeps": keeps,
    }


if __name__ == "__main__":
    import sys

    print(json.dumps(plan(sys.argv[1]), indent=2))
