"""Verify a rendered deliverable instead of trusting the render call."""
from __future__ import annotations

import json
import os
import subprocess


def ffprobe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {out.stderr.strip()[:200]}")
    data = json.loads(out.stdout)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    if not video:
        raise RuntimeError("no video stream in output")
    num, den = (video.get("r_frame_rate") or "0/1").split("/")
    fps = float(num) / float(den) if float(den) else 0.0
    return {
        "path": path,
        "bytes": os.path.getsize(path),
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": round(fps, 3),
        "frames": int(video.get("nb_frames") or 0),
        "duration": round(float(data["format"]["duration"]), 3),
        "vcodec": video.get("codec_name"),
        "has_audio": audio is not None,
        "acodec": audio.get("codec_name") if audio else None,
    }


def check(path: str, expect: dict) -> dict:
    """expect: {width, height, fps, duration, min_frames, has_audio}"""
    info = ffprobe(path)
    problems = []
    if expect.get("width") and info["width"] != expect["width"]:
        problems.append(f"width {info['width']} != {expect['width']}")
    if expect.get("height") and info["height"] != expect["height"]:
        problems.append(f"height {info['height']} != {expect['height']}")
    if expect.get("fps") and abs(info["fps"] - expect["fps"]) > 0.01:
        problems.append(f"fps {info['fps']} != {expect['fps']}")
    if expect.get("duration") and abs(info["duration"] - expect["duration"]) > 0.5:
        problems.append(f"duration {info['duration']}s != ~{expect['duration']}s")
    if expect.get("min_frames") and info["frames"] < expect["min_frames"]:
        problems.append(f"only {info['frames']} frames (expected {expect['min_frames']}+)")
    if expect.get("has_audio") and not info["has_audio"]:
        problems.append("no audio stream")
    if info["bytes"] < 20_000:
        problems.append(f"file suspiciously small ({info['bytes']} bytes)")
    return {"ok": not problems, "problems": problems, "info": info}


def frame(path: str, at_seconds: float, out_png: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(at_seconds), "-i", path,
                    "-frames:v", "1", out_png], check=True, timeout=120)
    return out_png
