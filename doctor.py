#!/usr/bin/env python3
"""Environment doctor — run this first on any machine.

    py -3.12 doctor.py

Exit code 0 = ready to build. Non-zero = something blocking is missing.
"""
from __future__ import annotations

import importlib
import os
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from ps import paths as machine  # noqa: E402

OK, WARN, FAIL = "ok", "warn", "fail"
rows: list[tuple[str, str, str]] = []


def add(state: str, name: str, detail: str = "") -> None:
    rows.append((state, name, detail))


def check_python() -> None:
    v = sys.version_info
    bits = 64 if sys.maxsize > 2**32 else 32
    if v[:2] >= (3, 12) and bits == 64:
        add(OK, "python", f"{platform.python_version()} 64-bit")
    else:
        add(FAIL, "python", f"{platform.python_version()} {bits}-bit — needs 3.12+ 64-bit "
                            "(3.11 segfaults on fusionscript.dll)")


def check_deps() -> None:
    for mod, pip_name in (("opentimelineio", "opentimelineio"), ("PIL", "pillow")):
        try:
            m = importlib.import_module(mod)
            add(OK, f"dep {pip_name}", getattr(m, "__version__", "installed"))
        except Exception:
            add(FAIL, f"dep {pip_name}", f"missing — py -3.12 -m pip install {pip_name}")


def check_tools() -> None:
    for name, path in machine.binaries().items():
        add(OK if path else FAIL, name, path or "not on PATH")


def check_resolve_install() -> dict:
    d = machine.discover()
    add(OK if d["api_dir"] else FAIL, "resolve scripting api", d["api_dir"] or "not found")
    add(OK if d["lib"] else FAIL, "fusionscript lib", d["lib"] or "not found")
    add(OK if d["exe"] else WARN, "resolve executable", d["exe"] or "not found (start Resolve manually)")
    return d


def check_scripting_mode() -> None:
    if platform.system() != "Windows":
        return
    cfg = os.path.join(os.environ.get("APPDATA", ""), "Blackmagic Design", "DaVinci Resolve",
                       "Preferences", ".config.data")
    if not os.path.exists(cfg):
        add(WARN, "external scripting", "preferences not found (run Resolve once)")
        return
    try:
        with open(cfg, "rb") as f:
            data = f.read().decode("utf-8", "ignore")
        if "System.Scripting.Mode = 1" in data or "System.Scripting.Mode = 2" in data:
            add(OK, "external scripting", "enabled")
        else:
            add(WARN, "external scripting", "may be disabled — Preferences > System > General")
    except Exception as e:
        add(WARN, "external scripting", f"unreadable ({e})")


def check_resolve_live() -> None:
    """Only if Resolve is already open — never launch it just to run the doctor."""
    running = "Resolve.exe" in subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Resolve.exe"], capture_output=True, text=True
    ).stdout if platform.system() == "Windows" else True
    if not running:
        add(WARN, "resolve live", "not running (start it before building)")
        return
    try:
        machine.apply_env()
        import DaVinciResolveScript as dvr
        app = dvr.scriptapp("Resolve")
        if not app:
            add(WARN, "resolve live", "running but not scriptable")
            return
        edition = app.GetProductName()
        version = ".".join(str(v) for v in app.GetVersion()[:3])
        add(OK, "resolve live", f"{edition} {version}")
        project = app.GetProjectManager().CreateProject("__PS_DOCTOR__")
        if project:
            presets = project.GetRenderPresetList() or []
            add(OK if "TikTok - 1080p" in presets else WARN, "render presets",
                f"{len(presets)} available" + ("" if "TikTok - 1080p" in presets else ", no TikTok preset"))
            app.GetProjectManager().CloseProject(project)
            app.GetProjectManager().DeleteProject("__PS_DOCTOR__")
    except Exception as e:
        add(WARN, "resolve live", f"{type(e).__name__}: {str(e)[:80]}")


def check_assets() -> None:
    font = os.path.join(HERE, "assets", "fonts", "Montserrat-ExtraBold.ttf")
    add(OK if os.path.exists(font) else WARN, "brand font",
        "Montserrat variable" if os.path.exists(font) else "missing — cards fall back")
    env = os.path.join(HERE, ".env")
    key = os.environ.get("FAL_KEY")
    if key:
        add(OK, "fal key", "environment")
    elif os.path.exists(env) and "FAL_KEY" in open(env, encoding="utf-8").read():
        add(OK, "fal key", ".env")
    else:
        add(WARN, "fal key", "absent — generation unavailable (.env FAL_KEY=...)")


def check_disk() -> None:
    free = shutil.disk_usage(HERE).free / 1e9
    add(OK if free > 20 else WARN, "disk free", f"{free:.0f} GB on this volume")


def check_gpu() -> None:
    if platform.system() != "Windows":
        return
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_VideoController | Select-Object -First 1).Name"],
            capture_output=True, text=True, timeout=30).stdout.strip()
        if out:
            add(OK, "gpu", out)
    except Exception:
        pass


def main() -> int:
    print("PixelSpryte doctor")
    check_python()
    check_deps()
    check_tools()
    check_resolve_install()
    check_scripting_mode()
    check_resolve_live()
    check_assets()
    check_disk()
    check_gpu()

    width = max(len(r[1]) for r in rows) + 2
    icon = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL "}
    for state, name, detail in rows:
        print(f"[{icon[state]}] {name:<{width}} {detail}")

    fails = [r for r in rows if r[0] == FAIL]
    warns = [r for r in rows if r[0] == WARN]
    print(f"\n{len(rows) - len(fails) - len(warns)} ok · {len(warns)} warnings · {len(fails)} blocking")
    if fails:
        print("Fix the FAIL rows before building.")
        return 1
    print("Ready to build.  py -3.12 build_reel.py reels/demo/brief.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
