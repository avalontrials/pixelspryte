#!/usr/bin/env python3
"""Package PixelSpryte for another machine.

Zips exactly the git-tracked files (no build outputs, no secrets), plus writes a
git bundle so the history travels too.

    py -3.12 package.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=HERE, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("not a git repo — run: git init && git add -A && git commit")
    return [line for line in out.stdout.splitlines() if line.strip()]


def main() -> int:
    os.makedirs(DIST, exist_ok=True)
    files = tracked()
    zip_path = os.path.join(DIST, "PixelSpryte-portable.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(os.path.join(HERE, f), arcname=os.path.join("PixelSpryte", f))
    size = os.path.getsize(zip_path) / 1e6
    print(f"zip:    {zip_path}  ({size:.2f} MB, {len(files)} files)")

    bundle = os.path.join(DIST, "PixelSpryte.bundle")
    r = subprocess.run(["git", "bundle", "create", bundle, "--all"], cwd=HERE,
                       capture_output=True, text=True)
    if r.returncode == 0:
        print(f"bundle: {bundle}  ({os.path.getsize(bundle)/1e6:.2f} MB)")
    else:
        print(f"bundle skipped: {r.stderr.strip()[:120]}")

    print("\nOn the target machine:")
    print("  1. unzip, cd PixelSpryte")
    print("  2. powershell -ExecutionPolicy Bypass -File setup.ps1")
    print("  3. put FAL_KEY=... in .env (optional, for generation)")
    print("  4. py -3.12 build_reel.py reels/demo/brief.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
