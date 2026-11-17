#!/usr/bin/env python3
"""PixelSpryte app - a small local window in your browser.

Run:  py -3.12 app.py
Then open http://127.0.0.1:8765

No pip installs. It uses only the Python standard library.
It calls build_reel.py, so DaVinci Resolve must be open.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(HERE, "app")
OUT_DIR = os.path.join(HERE, "out")
# PIXELSPRYTE_REELS lets you point the app at another folder (a demo, a client
# set) without touching the projects in reels/.
REELS_DIR = os.environ.get("PIXELSPRYTE_REELS") or os.path.join(HERE, "reels")
PORT = int(os.environ.get("PIXELSPRYTE_PORT") or 8765)

FORMATS = {
    "reel": {"w": 1080, "h": 1920, "label": "Vertical 9:16", "use": "Reels, Shorts, TikTok"},
    "portrait": {"w": 1080, "h": 1350, "label": "Portrait 4:5", "use": "Feed posts"},
    "square": {"w": 1080, "h": 1080, "label": "Square 1:1", "use": "Grid posts"},
    "wide": {"w": 1920, "h": 1080, "label": "Wide 16:9", "use": "YouTube"},
}

JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def python_cmd() -> list[str]:
    exe = shutil.which("py")
    if exe:
        return [exe, "-3.12"]
    return [sys.executable]


def slug_list() -> list[str]:
    if not os.path.isdir(REELS_DIR):
        return []
    out = []
    for name in sorted(os.listdir(REELS_DIR)):
        if os.path.isfile(os.path.join(REELS_DIR, name, "brief.json")):
            out.append(name)
    return out


def read_brief(slug: str) -> dict:
    with open(os.path.join(REELS_DIR, slug, "brief.json"), encoding="utf-8") as fh:
        return json.load(fh)


def find_video(slug: str) -> str | None:
    folder = os.path.join(OUT_DIR, slug)
    if not os.path.isdir(folder):
        return None
    best = None
    for name in os.listdir(folder):
        if name.lower().endswith(".mp4"):
            path = os.path.join(folder, name)
            if best is None or os.path.getmtime(path) > os.path.getmtime(best):
                best = path
    return best


def project_info(slug: str) -> dict:
    brief = read_brief(slug)
    canvas = brief.get("canvas") or {}
    video = find_video(slug)
    poster = None
    folder = os.path.join(OUT_DIR, slug)
    for candidate in ("review/frame_00_1.83s.png", "review/frame_00_0.75s.png",
                      "review/frame_00_0.50s.png", "cover.jpg",
                      "review/contact-sheet.jpg"):
        if os.path.isfile(os.path.join(folder, candidate)):
            poster = "/out/" + slug + "/" + candidate
            break
    if poster is None:
        review_dir = os.path.join(folder, "review")
        if os.path.isdir(review_dir):
            frames = sorted(f for f in os.listdir(review_dir) if f.startswith("frame_00"))
            if frames:
                poster = "/out/" + slug + "/review/" + frames[0]
    return {
        "slug": slug,
        "name": brief.get("name") or slug,
        "w": canvas.get("w", 0),
        "h": canvas.get("h", 0),
        "fps": canvas.get("fps", 30),
        "video": ("/out/" + slug + "/" + os.path.basename(video)) if video else None,
        "poster": poster,
        "review": ("/out/" + slug + "/review.html") if os.path.isfile(os.path.join(OUT_DIR, slug, "review.html")) else None,
        "brief": brief,
    }


def resolve_running() -> bool:
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Resolve.exe"],
            capture_output=True, text=True, timeout=15,
        ).stdout
        return "Resolve.exe" in out
    except Exception:
        return False


def run_build(job_id: str, slug: str) -> None:
    job = JOBS[job_id]
    cmd = python_cmd() + ["build_reel.py", os.path.join("reels", slug, "brief.json")]
    job["log"].append("$ " + " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        job["proc"] = proc
        for line in proc.stdout:
            job["log"].append(line.rstrip())
        job["code"] = proc.wait()
    except Exception as exc:  # noqa: BLE001
        job["log"].append("Could not start: " + str(exc))
        job["code"] = -1
    finally:
        job["running"] = False


def write_recording_brief(slug: str, name: str, fmt: str, keeps: list) -> None:
    brief = {
        "slug": slug,
        "name": name,
        "canvas": {"w": FORMATS[fmt]["w"], "h": FORMATS[fmt]["h"], "fps": 30},
        "footage": [{"path": "source.mp4", "in": a, "out": b} for a, b in keeps],
        "captions_mode": "none",
        "render": {
            "preset": "YouTube - 1080p" if fmt == "wide" else "TikTok - 1080p",
            "name": slug + "_v1",
        },
    }
    with open(os.path.join(REELS_DIR, slug, "brief.json"), "w", encoding="utf-8") as fh:
        json.dump(brief, fh, indent=2)


def run_recording(job_id: str, slug: str, name: str, fmt: str, pause: float) -> None:
    from ps import tightcut

    job = JOBS[job_id]
    source = os.path.join(REELS_DIR, slug, "source.mp4")
    try:
        job["log"].append("Listening for pauses...")
        plan = tightcut.plan(source, min_silence=float(pause))
        job["log"].append(
            f"  {plan['source_seconds']:.1f}s of footage -> {plan['kept_seconds']:.1f}s kept, "
            f"{plan['cuts']} cuts"
        )
        for a, b in plan["keeps"]:
            job["log"].append(f"  keep {a:6.2f}s - {b:6.2f}s")
        write_recording_brief(slug, name, fmt, plan["keeps"])
        job["log"].append("  brief written. Building in Resolve...")
        run_build(job_id, slug)
    except Exception as exc:  # noqa: BLE001
        job["log"].append("Could not cut it: " + str(exc))
        job["code"] = -1
        job["running"] = False



class Handler(BaseHTTPRequestHandler):
    server_version = "PixelSpryteApp/1.0"

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    # ------------------------------------------------------------- helpers
    def send_json(self, payload, code=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        if not os.path.isfile(path):
            self.send_json({"error": "not found"}, 404)
            return
        ext = os.path.splitext(path)[1].lower()
        types = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".mp4": "video/mp4",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".otio": "application/json",
            ".srt": "text/plain; charset=utf-8",
            ".md": "text/plain; charset=utf-8",
        }
        ctype = types.get(ext, "application/octet-stream")
        size = os.path.getsize(path)

        # Browsers ask for byte ranges when you scrub a video.
        start, end = 0, size - 1
        status = 200
        header = self.headers.get("Range")
        if header and header.startswith("bytes=") and size:
            try:
                spec = header[len("bytes="):].split(",")[0]
                a, _, b = spec.partition("-")
                if a.strip() == "":
                    start = max(0, size - int(b))
                    end = size - 1
                else:
                    start = int(a)
                    end = int(b) if b.strip() else size - 1
                end = min(end, size - 1)
                if start > end:
                    start, end = 0, size - 1
                else:
                    status = 206
            except ValueError:
                start, end, status = 0, size - 1, 200

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                chunk = fh.read(min(65536, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # ---------------------------------------------------------------- routes
    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/" or path == "/index.html":
            return self.send_file(os.path.join(APP_DIR, "index.html"))
        if path.startswith("/app/"):
            name = os.path.basename(path)
            return self.send_file(os.path.join(APP_DIR, name))
        if path.startswith("/out/"):
            rel = path[len("/out/"):]
            full = os.path.normpath(os.path.join(OUT_DIR, rel))
            if not full.startswith(os.path.normpath(OUT_DIR) + os.sep):
                return self.send_json({"error": "bad path"}, 400)
            return self.send_file(full)

        if path == "/api/projects":
            items = []
            for slug in slug_list():
                try:
                    items.append(project_info(slug))
                except Exception:
                    continue
            return self.send_json({"projects": items, "formats": FORMATS, "resolve": resolve_running()})

        if path == "/api/project":
            slug = parse_qs(parsed.query).get("slug", [""])[0]
            if not SLUG_OK.match(slug):
                return self.send_json({"error": "bad slug"}, 400)
            return self.send_json(project_info(slug))

        if path.startswith("/api/job/"):
            job = JOBS.get(path.rsplit("/", 1)[-1])
            if not job:
                return self.send_json({"error": "no job"}, 404)
            return self.send_json({
                "running": job["running"],
                "code": job.get("code"),
                "log": job["log"][-300:],
            })

        return self.send_json({"error": "not found"}, 404)

    def handle_recording(self, parsed):
        q = parse_qs(parsed.query)
        name = str(q.get("name", [""])[0]).strip()
        fmt = str(q.get("format", ["reel"])[0])
        pause = str(q.get("pause", ["0.7"])[0])
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
        if not slug or not SLUG_OK.match(slug):
            return self.send_json({"error": "Please type a simple name, like my-first-reel."}, 400)
        if slug in slug_list():
            return self.send_json({"error": "That name is already used."}, 400)
        if fmt not in FORMATS:
            return self.send_json({"error": "Unknown shape."}, 400)
        if not resolve_running():
            return self.send_json({"error": "Open DaVinci Resolve first, then try again."})

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self.send_json({"error": "No file arrived."}, 400)
        if length > 4 * 1024 ** 3:
            return self.send_json({"error": "That file is bigger than 4 GB."}, 400)

        folder = os.path.join(REELS_DIR, slug)
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, "source.mp4")
        left = length
        with open(dest, "wb") as fh:
            while left > 0:
                chunk = self.rfile.read(min(1024 * 1024, left))
                if not chunk:
                    break
                fh.write(chunk)
                left -= len(chunk)

        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = {"running": True, "log": [], "code": None, "started": time.time()}
        threading.Thread(
            target=run_recording,
            args=(job_id, slug, name, fmt, float(pause)),
            daemon=True,
        ).start()
        return self.send_json({"job": job_id, "slug": slug})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # The recording upload is raw bytes, so it must not go through read_json.
        if path == "/api/from-recording":
            return self.handle_recording(parsed)

        data = self.read_json()

        if path == "/api/build":
            slug = str(data.get("slug", ""))
            if not SLUG_OK.match(slug):
                return self.send_json({"error": "bad slug"}, 400)
            if not resolve_running():
                return self.send_json({
                    "error": "Open DaVinci Resolve first. Then press Make it again."
                })
            job_id = uuid.uuid4().hex[:12]
            JOBS[job_id] = {"running": True, "log": [], "code": None, "started": time.time()}
            threading.Thread(target=run_build, args=(job_id, slug), daemon=True).start()
            return self.send_json({"job": job_id})

        if path == "/api/new":
            name = str(data.get("name", "")).strip()
            fmt = str(data.get("format", "reel"))
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
            if not slug or not SLUG_OK.match(slug):
                return self.send_json({"error": "Please type a simple name, like my-first-reel."}, 400)
            if slug in slug_list():
                return self.send_json({"error": "That name is already used."}, 400)
            if fmt not in FORMATS:
                return self.send_json({"error": "Unknown format."}, 400)
            os.makedirs(os.path.join(REELS_DIR, slug), exist_ok=True)
            template = os.path.join(REELS_DIR, "demo", "brief.json")
            with open(template, encoding="utf-8") as fh:
                brief = json.load(fh)
            brief["slug"] = slug
            brief["name"] = name
            brief["canvas"] = {"w": FORMATS[fmt]["w"], "h": FORMATS[fmt]["h"], "fps": 30}
            brief["render"] = brief.get("render") or {}
            # The preset sets codec and container; FormatWidth/FormatHeight in
            # ps/resolve.py make the render match the canvas.
            brief["render"]["preset"] = "YouTube - 1080p" if fmt == "wide" else "TikTok - 1080p"
            brief["render"]["name"] = slug + "_v1"
            with open(os.path.join(REELS_DIR, slug, "brief.json"), "w", encoding="utf-8") as fh:
                json.dump(brief, fh, indent=2)
            return self.send_json(project_info(slug))

        return self.send_json({"error": "not found"}, 404)


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(REELS_DIR, exist_ok=True)
    url = f"http://127.0.0.1:{PORT}"
    print("PixelSpryte app")
    print("  " + url)
    print("  Press Ctrl+C to stop.")
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
