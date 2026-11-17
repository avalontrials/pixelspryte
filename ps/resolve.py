"""PixelSpryte — Resolve connection layer.

Every rule in here was verified against DaVinci Resolve Studio 20.0.0.49 on
Windows. Do not "clean up" the quirks; they are load-bearing.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

from . import paths as machine

_discovered = machine.discover()
RESOLVE_API = _discovered["api_dir"] or ""
RESOLVE_LIB = _discovered["lib"] or ""
RESOLVE_EXE = _discovered["exe"] or ""

# A Resolve timeline starts at 01:00:00:00. Passing recordFrame=0 puts clips
# outside it and silently yields one-frame renders, so we never pass it.
TIMELINE_START = 86400


def _prepare_env() -> None:
    machine.apply_env()


def _is_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Resolve.exe"],
                             capture_output=True, text=True, timeout=20).stdout
        return "Resolve.exe" in out
    except Exception:
        return False


class Resolve:
    def __init__(self, autostart: bool = True, startup_timeout: int = 120):
        _prepare_env()
        self.autostart = autostart
        self.startup_timeout = startup_timeout
        self.app = None

    # ---------------------------------------------------------------- connect
    def connect(self) -> "Resolve":
        import DaVinciResolveScript as dvr  # noqa: N813
        self._dvr = dvr
        self.app = dvr.scriptapp("Resolve")
        if self.app:
            return self
        if not self.autostart:
            raise RuntimeError("DaVinci Resolve is not running.")
        if not _is_running():
            if not RESOLVE_EXE:
                raise RuntimeError(
                    "Resolve is not running and its executable was not found on this machine. "
                    "Start DaVinci Resolve manually, or set RESOLVE_EXE.")
            subprocess.Popen([RESOLVE_EXE])
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            time.sleep(3)
            self.app = dvr.scriptapp("Resolve")
            if self.app:
                time.sleep(3)  # let the project manager settle
                return self
        raise RuntimeError("Could not connect to DaVinci Resolve.")

    @property
    def version(self) -> str:
        return " ".join(str(v) for v in self.app.GetVersion())

    # ---------------------------------------------------------------- projects
    def project(self, name: str, width: int, height: int, fps: int):
        """Create (or load) a project with the canvas already correct.

        Resolve answers GetProjectManager() before it is ready to answer
        CreateProject(); the object comes back with a None method and the call
        raises TypeError. Wait for it instead of failing the build.
        """
        project = None
        last: Exception | None = None
        deadline = time.time() + 45
        while time.time() < deadline:
            pm = self.app.GetProjectManager()
            if pm is not None:
                try:
                    project = pm.CreateProject(name)
                    if not project:
                        project = pm.LoadProject(name)
                except TypeError as exc:  # project manager still warming up
                    last = exc
            if project:
                break
            time.sleep(3)
        if not project:
            raise RuntimeError(f"Could not create or load project: {name} ({last})")
        project.SetSetting("timelineFrameRate", str(fps))
        project.SetSetting("timelineResolutionWidth", str(width))
        project.SetSetting("timelineResolutionHeight", str(height))
        return _Project(self.app, pm, project, name)

    def quit(self) -> None:
        try:
            self.app.Quit()
        except Exception:
            pass


class _Project:
    def __init__(self, app, pm, project, name):
        self.app, self.pm, self.p, self.name = app, pm, project, name

    # ------------------------------------------------------------------ media
    def media_pool(self):
        pool = self.p.GetMediaPool()
        if not pool:
            raise RuntimeError("Media pool unavailable (is a project open?)")
        return pool

    def import_timeline(self, otio_path: str, timeline_name: str):
        """Import an OTIO/AAF/FCPXML/EDL file as a timeline. Returns Timeline.

        OTIO stack order maps 1:1 to Resolve track numbers: the FIRST track in
        the stack becomes V1 (bottom). Put graphics last so they sit on V2.
        """
        tl = None
        pool = self.media_pool()
        # A duplicate timeline name makes ImportTimelineFromFile return None.
        for i in range(1, (self.p.GetTimelineCount() or 0) + 1):
            existing = self.p.GetTimelineByIndex(i)
            if existing and existing.GetName() == timeline_name:
                pool.DeleteTimelines([existing])
                break
        tl = pool.ImportTimelineFromFile(
            otio_path, {"timelineName": timeline_name, "importSourceClips": True}
        )
        if not tl:  # last resort: unique name, then report it
            alt = f"{timeline_name}_{int(time.time()) % 10000}"
            tl = pool.ImportTimelineFromFile(
                otio_path, {"timelineName": alt, "importSourceClips": True})
            if tl:
                print(f"  warning: imported as '{alt}' (name '{timeline_name}' was taken)")
        if not tl:
            raise RuntimeError(f"Timeline import failed: {otio_path}")
        self.p.SetCurrentTimeline(tl)
        return tl

    # --------------------------------------------------------------- captions
    def add_subtitle_track(self, timeline):
        if timeline.GetTrackCount("subtitle") < 1:
            timeline.AddTrack("subtitle")
        return timeline.GetTrackCount("subtitle")

    def append_srt(self, timeline, srt_path: str) -> int:
        """Push an SRT onto the subtitle track.

        Resolve has no AddSubtitle call, but an .srt imports as a Subtitle
        media item and appends cleanly with its own text and timings.
        """
        srt_path = os.path.abspath(srt_path)
        items = self.media_pool().ImportMedia([srt_path])
        if not items:
            raise RuntimeError(f"SRT import failed: {srt_path}")
        sub = items[0]
        if sub.GetClipProperty("Type") != "Subtitle":
            raise RuntimeError("SRT did not import as a Subtitle clip")
        self.add_subtitle_track(timeline)
        self.media_pool().AppendToTimeline([{"mediaPoolItem": sub}])
        return len(timeline.GetItemListInTrack("subtitle", 1) or [])

    # ---------------------------------------------------------------- framing
    def frame_fill_tracks(self, timeline, tracks: list[int], mode: str = "fill",
                          limit: float = 8.0) -> list[dict]:
        rows = []
        for track in tracks:
            rows.extend(self.frame_fill(timeline, track=track, mode=mode, limit=limit))
        return rows

    def set_audio_volume(self, timeline, track: int, volume_db: float) -> int:
        """Set a constant volume on every audio item in a track (used to mute B-roll)."""
        n = 0
        for item in timeline.GetItemListInTrack("audio", track) or []:
            try:
                item.SetProperty("Volume", float(volume_db))
                n += 1
            except Exception:
                pass
        return n

    def frame_fill(self, timeline, track: int = 1, mode: str = "fill", limit: float = 8.0) -> list[dict]:
        """Scale footage so it covers the canvas (no letterbox/pillarbox)."""
        canvas_w = float(self.p.GetSetting("timelineResolutionWidth"))
        canvas_h = float(self.p.GetSetting("timelineResolutionHeight"))
        report = []
        for item in timeline.GetItemListInTrack("video", track) or []:
            # TimelineItem has no GetClipProperty; ask its media pool item.
            mp_item = item.GetMediaPoolItem()
            res = (mp_item.GetClipProperty("Resolution") if mp_item else "") or ""
            try:
                src_w, src_h = (float(x) for x in res.lower().split("x"))
            except Exception:
                report.append({"clip": item.GetName(), "skipped": f"no resolution ({res!r})"})
                continue
            fit = min(canvas_w / src_w, canvas_h / src_h)
            fill = max(canvas_w / src_w, canvas_h / src_h)
            zoom = 1.0 if mode == "fit" else round(fill / fit, 4)
            if zoom > limit:
                report.append({"clip": item.GetName(), "skipped": f"fill would upscale {zoom}x"})
                continue
            item.SetProperty("ZoomGang", True)
            item.SetProperty("ZoomX", zoom)
            item.SetProperty("ZoomY", zoom)
            report.append({"clip": item.GetName(), "zoom": zoom,
                           "readback": item.GetProperty("ZoomX")})
        return report

    # ----------------------------------------------------------------- render
    def render(self, out_dir: str, basename: str, preset: str = "TikTok - 1080p",
               width: int | None = None, height: int | None = None,
               fps: float | None = None) -> str:
        os.makedirs(out_dir, exist_ok=True)
        if not self.p.LoadRenderPreset(preset):
            raise RuntimeError(f"Render preset not found: {preset}")
        settings = {
            "SelectAllFrames": True,
            "TargetDir": os.path.abspath(out_dir),
            "CustomName": basename,
        }
        # The preset carries a resolution too, and Resolve has no square/4:5
        # preset. FormatWidth/FormatHeight override it so the render matches the
        # canvas the brief asked for instead of the preset's default.
        if width and height:
            settings["FormatWidth"] = int(width)
            settings["FormatHeight"] = int(height)
        if fps:
            settings["FrameRate"] = float(fps)
        if not self.p.SetRenderSettings(settings):
            raise RuntimeError("SetRenderSettings refused the format override")
        job = self.p.AddRenderJob()
        if not job:
            raise RuntimeError("AddRenderJob failed")
        if not self.p.StartRendering([job]):
            raise RuntimeError("StartRendering refused")
        started = time.time()
        while self.p.IsRenderingInProgress():
            time.sleep(2)
            if time.time() - started > 3600:
                raise RuntimeError("Render exceeded 1 hour")
        elapsed = time.time() - started
        jobs = {j["JobId"]: j for j in (self.p.GetRenderJobList() or [])}
        info = jobs.get(job, {})
        path = os.path.join(out_dir, info.get("OutputFilename", ""))
        if not os.path.exists(path):
            candidates = [os.path.join(out_dir, f) for f in os.listdir(out_dir)
                          if f.startswith(basename)]
            if not candidates:
                raise RuntimeError("Render finished but no output file was written")
            path = max(candidates, key=os.path.getmtime)
        try:
            self.p.DeleteRenderJob(job)
        except Exception:
            pass
        return path, elapsed

    # ---------------------------------------------------------------- closing
    def close(self, delete: bool = False) -> None:
        try:
            self.pm.CloseProject(self.pm.GetCurrentProject())
        except Exception:
            pass
        if delete:
            self.pm.DeleteProject(self.name)
