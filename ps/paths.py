"""Machine discovery — nothing in PixelSpryte assumes a particular box.

Resolve's install paths differ between machines and editions. Everything is
resolved here, honouring environment overrides first, so the same folder runs
on the workstation and on the laptop.
"""
from __future__ import annotations

import os
import platform
import shutil

SCRIPT_SUBDIR = ("Support", "Developer", "Scripting")


def _first_existing(*paths: str) -> str | None:
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def discover() -> dict:
    """Return {api_dir, lib, exe, platform} for the local machine."""
    system = platform.system()
    api = os.environ.get("RESOLVE_SCRIPT_API")
    lib = os.environ.get("RESOLVE_SCRIPT_LIB")
    exe = os.environ.get("RESOLVE_EXE")

    if system == "Windows":
        api = api or _first_existing(
            os.path.join(r"C:\ProgramData\Blackmagic Design\DaVinci Resolve", *SCRIPT_SUBDIR),
            os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                         "Blackmagic Design", "DaVinci Resolve", *SCRIPT_SUBDIR),
        )
        lib = lib or _first_existing(
            r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll",
        )
        exe = exe or _first_existing(
            r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe",
        )
    elif system == "Darwin":
        api = api or _first_existing(
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting")
        lib = lib or _first_existing(
            "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so")
        exe = exe or _first_existing("/Applications/DaVinci Resolve/DaVinci Resolve.app")
    else:
        api = api or _first_existing("/opt/resolve/Developer/Scripting")
        lib = lib or _first_existing("/opt/resolve/libs/Fusion/fusionscript.so")
        exe = exe or _first_existing("/opt/resolve/bin/resolve")

    return {
        "platform": system,
        "api_dir": api,
        "modules_dir": os.path.join(api, "Modules") if api else None,
        "lib": lib,
        "exe": exe,
    }


def apply_env() -> dict:
    """Export what the Resolve module loader expects and put Modules on sys.path."""
    import sys

    paths = discover()
    if paths["api_dir"]:
        os.environ.setdefault("RESOLVE_SCRIPT_API", paths["api_dir"])
    if paths["lib"]:
        os.environ.setdefault("RESOLVE_SCRIPT_LIB", paths["lib"])
    if paths["modules_dir"] and paths["modules_dir"] not in sys.path:
        sys.path.insert(0, paths["modules_dir"])
    return paths


def binaries() -> dict:
    """External tools the pipeline shells out to."""
    return {
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
    }
