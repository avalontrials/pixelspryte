#!/usr/bin/env python3
"""Generate synthetic 16:9 test footage so the pipeline can be exercised
before real assets land. Each clip has a distinct look and tone.
"""
import os
import subprocess

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "media")

CLIPS = [
    ("demo_a.mp4", "testsrc2=size=1280x720:rate=30:duration=4", 440),
    ("demo_b.mp4", "smptebars=size=1280x720:rate=30:duration=4", 523),
    ("demo_c.mp4", "testsrc=size=1280x720:rate=30:duration=4", 659),
]


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    for name, video_filter, tone in CLIPS:
        path = os.path.join(OUT, name)
        subprocess.run([
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", video_filter,
            "-f", "lavfi", "-i", f"sine=frequency={tone}:duration=4",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", path,
        ], check=True)
        print(f"  {name}: {os.path.getsize(path)/1e6:.2f} MB")


if __name__ == "__main__":
    main()
