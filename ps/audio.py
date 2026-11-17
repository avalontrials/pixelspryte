"""Audio assembly: place narration takes, duck a music bed, deliver one master.

Resolve can mix tracks, but scripting per-clip volume automation is fragile. A
pre-mixed 48 kHz stereo WAV gives a predictable, measurable master while the
individual takes remain on disk for re-edits.
"""
from __future__ import annotations

import json
import os
import subprocess


def duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {out.stderr.strip()[:200]}")
    return round(float(json.loads(out.stdout)["format"]["duration"]), 3)


def _inputs(narration: list[dict], music: dict | None) -> tuple[list[str], str]:
    args: list[str] = []
    for clip in narration:
        args += ["-i", clip["path"]]
    music_index = None
    if music:
        music_index = len(narration)
        if music.get("loop", True):
            args += ["-stream_loop", "-1"]
        args += ["-i", music["path"]]
    return args, music_index


def build_mix(narration: list[dict], out_path: str, total_seconds: float,
              music: dict | None = None, lufs: float = -14.0) -> str:
    """Mix narration (with `at` offsets) and optional ducked music.

    narration: [{"path": ..., "at": 0.0}, ...]
    music:     {"path": ..., "gain_db": -22, "loop": True}
    """
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    if not narration and not music:
        raise ValueError("nothing to mix")

    args, music_index = _inputs(narration, music)
    filters: list[str] = []
    labels: list[str] = []

    for i, clip in enumerate(narration):
        at_ms = int(round(float(clip.get("at", 0.0)) * 1000))
        gain = float(clip.get("gain_db", 0.0))
        label = f"n{i}"
        filters.append(
            f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"volume={gain}dB,adelay={at_ms}:all=1[{label}]"
        )
        labels.append(f"[{label}]")

    narration_label = None
    if labels:
        if len(labels) == 1:
            narration_label = labels[0].strip("[]")
        else:
            filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0[nar]")
            narration_label = "nar"

    music_label = None
    if music:
        gain = float(music.get("gain_db", -22.0))
        filters.append(
            f"[{music_index}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"atrim=0:{total_seconds:.3f},volume={gain}dB[m]"
        )
        music_label = "m"

    if narration_label and music_label:
        # A filter output can be consumed once. Split the narration: one copy
        # is the audible bed, the other drives the music ducking.
        filters.append(f"[{narration_label}]asplit=2[nar_mix][nar_sc]")
        filters.append(
            "[m][nar_sc]sidechaincompress=threshold=0.035:ratio=7:attack=18:release=420[duck]"
        )
        filters.append(
            f"[nar_mix][duck]amix=inputs=2:normalize=0,apad=whole_dur={total_seconds:.3f},"
            f"loudnorm=I={lufs}:TP=-1.5:LRA=11[out]"
        )
    elif narration_label:
        filters.append(
            f"[{narration_label}]apad=whole_dur={total_seconds:.3f},"
            f"loudnorm=I={lufs}:TP=-1.5:LRA=11[out]"
        )
    else:
        filters.append(
            f"[m]apad=whole_dur={total_seconds:.3f},"
            f"loudnorm=I={lufs}:TP=-1.5:LRA=11[out]"
        )

    cmd = ["ffmpeg", "-y", "-v", "error", *args,
           "-filter_complex", ";".join(filters),
           "-map", "[out]", "-t", f"{total_seconds:.3f}",
           "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", out_path]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        raise RuntimeError(f"audio mix failed: {proc.stderr.strip()[:1200]}")
    return out_path


def loudness(path: str) -> dict:
    """Measure integrated loudness/peak with ffmpeg's ebur128."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", path, "-filter_complex", "ebur128=peak=true",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=900,
    )
    text = proc.stderr
    integrated = peak = None
    for line in text.splitlines():
        if "I:" in line and "LUFS" in line:
            try:
                integrated = float(line.split("I:")[1].split("LUFS")[0].strip())
            except Exception:
                pass
        if "Peak:" in line and "dBFS" in line:
            try:
                peak = float(line.split("Peak:")[1].split("dBFS")[0].strip())
            except Exception:
                pass
    return {"integrated_lufs": integrated, "true_peak_dbfs": peak}
