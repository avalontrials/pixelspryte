"""Online B-roll: a small, licensed catalog plus a cache-first downloader.

The reel does not need generated footage for every second. Stock B-roll carries
the middle of the edit while the paid AI shots carry the character moments.
Every clip keeps its source page, license and measured duration so the manifest
can prove what was used.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request

from . import verify

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BROLL_DIR = os.path.join(HERE, "assets", "broll")
CATALOG_PATH = os.path.join(BROLL_DIR, "catalog.json")

# Curated for this reel. All are Mixkit Stock Video Free License items.
CATALOG = {
    "chicago-day": {
        "url": "https://assets.mixkit.co/videos/11395/11395-720.mp4",
        "source_page": "https://mixkit.co/free-stock-video/chicago-city-skyline-sunset-11395/",
        "license": "Mixkit Stock Video Free License",
        "license_url": "https://mixkit.co/license/#videoFree",
        "note": "Chicago skyline from the lake, daylight.",
    },
    "chicago-dusk": {
        "url": "https://assets.mixkit.co/videos/1773/1773-1080.mp4",
        "source_page": "https://mixkit.co/free-stock-video/chicago-city-at-late-afternoon-1773/",
        "license": "Mixkit Stock Video Free License",
        "license_url": "https://mixkit.co/license/#videoFree",
        "note": "Chicago skyline at late afternoon.",
    },
    "kitchen": {
        "url": "https://assets.mixkit.co/videos/43033/43033-1080.mp4",
        "source_page": "https://mixkit.co/free-stock-video/interior-view-of-a-spacious-modern-kitchen-43033/",
        "license": "Mixkit Stock Video Free License",
        "license_url": "https://mixkit.co/license/#videoFree",
        "note": "Bright modern kitchen interior.",
    },
    "living-room": {
        "url": "https://assets.mixkit.co/videos/3110/3110-1080.mp4",
        "source_page": "https://mixkit.co/free-stock-video/minimalist-room-with-gray-sofa-3110/",
        "license": "Mixkit Stock Video Free License",
        "license_url": "https://mixkit.co/license/#videoFree",
        "note": "Minimal staged living room.",
    },
    "calculator": {
        "url": "https://assets.mixkit.co/videos/4533/4533-1080.mp4",
        "source_page": "https://mixkit.co/free-stock-video/woman-doing-accounts-on-a-calculator-4533/",
        "license": "Mixkit Stock Video Free License",
        "license_url": "https://mixkit.co/license/#videoFree",
        "note": "Hands, paperwork and a calculator.",
    },
    "laptop": {
        "url": "https://assets.mixkit.co/videos/4938/4938-1080.mp4",
        "source_page": "https://mixkit.co/free-stock-video/hands-of-a-girl-working-on-a-computer-4938/",
        "license": "Mixkit Stock Video Free License",
        "license_url": "https://mixkit.co/license/#videoFree",
        "note": "Hands researching on a laptop.",
    },
    "neighborhood": {
        "url": "https://assets.mixkit.co/videos/24945/24945-720.mp4",
        "source_page": "https://mixkit.co/free-stock-video/modern-houses-at-the-edge-of-town-24945/",
        "license": "Mixkit Stock Video Free License",
        "license_url": "https://mixkit.co/license/#videoFree",
        "note": "Residential houses and a quiet street.",
    },
    "couple": {
        "url": "https://assets.mixkit.co/videos/939/939-1080.mp4",
        "source_page": "https://mixkit.co/free-stock-video/couple-silhouette-walking-through-a-neighborhood-939/",
        "license": "Mixkit Stock Video Free License",
        "license_url": "https://mixkit.co/license/#videoFree",
        "note": "Couple walking through a neighborhood at dusk.",
    },
    "r11-terrace": {
        "url": "https://assets.mixkit.co/videos/4029/4029-1080.mp4",
        "source_page": "https://mixkit.co/free-stock-video/interior-of-a-room-with-terrace-4029/",
        "license": "Mixkit Stock Video Free License",
        "license_url": "https://mixkit.co/license/#videoFree",
        "note": "Luxury room with a terrace; new for reel 11.",
    },
    "r11-handshake": {
        "url": "https://assets.mixkit.co/videos/5889/5889-1080.mp4",
        "source_page": "https://mixkit.co/free-stock-video/handshake-between-couple-5889/",
        "license": "Mixkit Stock Video Free License",
        "license_url": "https://mixkit.co/license/#videoFree",
        "note": "Handshake close-up; new for reel 11.",
    },
    "r11-key": {
        "url": "https://assets.mixkit.co/videos/12877/12877-720.mp4",
        "source_page": "https://mixkit.co/free-stock-video/slow-motion-offer-of-house-key-and-handshake-12877/",
        "license": "Mixkit Stock Video Free License",
        "license_url": "https://mixkit.co/license/#videoFree",
        "note": "House key handoff; only 720p on Mixkit.",
    },
    "r11-luxury": {
        "url": "https://assets.mixkit.co/videos/26006/26006-720.mp4",
        "source_page": "https://mixkit.co/free-stock-video/relaxing-in-a-luxury-apartment-26006/",
        "license": "Mixkit Stock Video Free License",
        "license_url": "https://mixkit.co/license/#videoFree",
        "note": "Luxury apartment interior; only 720p on Mixkit.",
    },
}


def _catalog() -> dict:
    if os.path.exists(CATALOG_PATH):
        try:
            saved = json.load(open(CATALOG_PATH, encoding="utf-8"))
            if isinstance(saved, dict):
                return {**CATALOG, **saved}
        except Exception:
            pass
    return dict(CATALOG)


def _download(url: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=300) as r, open(dest, "wb") as f:
            f.write(r.read())
    except Exception:
        curl = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe")
        if not os.path.exists(curl):
            raise
        subprocess.run([curl, "--fail", "--location", "--silent", "--show-error",
                        "--output", dest, url], check=True, timeout=900)


def fetch(clip_id: str, force: bool = False) -> dict:
    """Return {id, path, source_page, license, duration, width, height}."""
    catalog = _catalog()
    if clip_id not in catalog:
        raise KeyError(f"unknown b-roll id: {clip_id}")
    entry = dict(catalog[clip_id])
    entry["id"] = clip_id
    dest = os.path.join(BROLL_DIR, f"{clip_id}.mp4")
    entry["path"] = dest
    if force or not os.path.exists(dest) or os.path.getsize(dest) < 100_000:
        _download(entry["url"], dest)
    info = verify.ffprobe(dest)
    entry.update({
        "duration": info["duration"],
        "width": info["width"],
        "height": info["height"],
        "bytes": info["bytes"],
    })
    return entry


def fetch_all(ids: list[str]) -> dict[str, dict]:
    return {cid: fetch(cid) for cid in ids}
