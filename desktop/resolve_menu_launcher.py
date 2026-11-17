# PixelSpryte launcher — installed into DaVinci Resolve's Scripts menu by
# install.bat (Workspace > Scripts > PixelSpryte). The installer replaces
# __APP_DIR__ with the real install path.
import os
import subprocess
import sys

APP_DIR = r"__APP_DIR__"


def _find_pythonw():
    # Resolve runs scripts under its own interpreter; prefer a pythonw
    # next to it, else fall back to whatever is on PATH.
    exe = sys.executable or ""
    candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(candidate):
        return candidate
    return "pythonw.exe"


ui = os.path.join(APP_DIR, "ui.py")
if os.path.exists(ui):
    subprocess.Popen([_find_pythonw(), ui], cwd=APP_DIR,
                     creationflags=0x00000008)  # DETACHED_PROCESS
else:
    print(f"PixelSpryte not found at {APP_DIR}")
