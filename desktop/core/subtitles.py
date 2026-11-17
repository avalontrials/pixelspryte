"""
PixelSpryte Subtitle Builder
Generates an .srt sidecar for the EDITED timeline by remapping the
source-time Whisper transcript through the EDL's keep ranges.

The .srt is written next to the render — upload it with the video
(YouTube/LinkedIn accept .srt directly). For burned-in captions use
Resolve's AI subtitles (driven by resolve_driver.create_ai_subtitles).
"""

import os
from typing import List, Dict


def _srt_time(seconds: float) -> str:
    """Format seconds as SRT timestamp (HH:MM:SS,mmm)."""
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_edit_srt(
    transcript_segments: List[Dict],
    keep_segments: List[Dict],
    min_duration: float = 0.2,
) -> str:
    """
    Remap transcript segments (source time) onto the edited timeline.

    For each keep range, transcript segments that overlap it are clipped
    to the range and shifted by the cumulative timeline offset.

    Args:
        transcript_segments: dicts with start, end, and text or
                             transcript_excerpt (Whisper output).
        keep_segments: EDL keep dicts with keep_start / keep_end,
                       in timeline order.

    Returns:
        SRT file content as a string.
    """
    entries = []
    offset = 0.0

    for keep in keep_segments:
        ks = keep.get("keep_start", 0.0)
        ke = keep.get("keep_end", 0.0)

        for seg in transcript_segments:
            s, e = seg.get("start", 0.0), seg.get("end", 0.0)
            if e <= ks or s >= ke:
                continue
            text = (seg.get("text") or seg.get("transcript_excerpt") or "").strip()
            if not text:
                continue

            cs, ce = max(s, ks), min(e, ke)
            tl_start = offset + (cs - ks)
            tl_end = offset + (ce - ks)
            if tl_end - tl_start < min_duration:
                continue
            entries.append((tl_start, tl_end, text))

        offset += max(0.0, ke - ks)

    lines = []
    for i, (start, end, text) in enumerate(entries, 1):
        lines.append(f"{i}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n")

    return "\n".join(lines)


def write_edit_srt(
    transcript_path_or_segments,
    keep_segments: List[Dict],
    output_path: str,
) -> str:
    """Build and write the .srt. Accepts a transcript JSON path or a list."""
    import json

    if isinstance(transcript_path_or_segments, str):
        with open(transcript_path_or_segments, "r") as f:
            transcript = json.load(f)
    else:
        transcript = transcript_path_or_segments

    srt = build_edit_srt(transcript, keep_segments)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(srt)

    n = srt.count(" --> ")
    print(f"  Subtitles: {n} entries -> {os.path.basename(output_path)}")
    return output_path
