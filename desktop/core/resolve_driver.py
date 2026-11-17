"""
PixelSpryte Resolve Driver — DaVinci Resolve 20 Studio
Connects to a running Resolve instance via the Python scripting API.
Reads an EDL plan (JSON) and builds a timeline with placed segments,
zoom transforms, and caption markers.

Requirements:
  - DaVinci Resolve 20 Studio running with scripting enabled
  - Python modules at the standard Resolve path on Windows
"""

import os
import sys
import json

# Resolve 20 scripting modules path (Windows)
RESOLVE_MODULES = (
    r"C:\ProgramData\Blackmagic Design"
    r"\DaVinci Resolve\Support\Developer\Scripting\Modules"
)
if RESOLVE_MODULES not in sys.path:
    sys.path.append(RESOLVE_MODULES)


# ─── Connection ─────────────────────────────────────────────────────

def get_resolve():
    """
    Connect to running DaVinci Resolve instance.
    Resolve must be open before calling this.
    Returns the Resolve scripting object.
    """
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        raise RuntimeError(
            f"Cannot import DaVinci Resolve Script modules.\n"
            f"Ensure Resolve 20 is installed and modules exist at:\n"
            f"  {RESOLVE_MODULES}\n"
            f"Also check: Preferences > System > General > External scripting = Local"
        )

    resolve = dvr.scriptapp("Resolve")
    if not resolve:
        raise RuntimeError(
            "Could not connect to DaVinci Resolve.\n"
            "Make sure Resolve is running before executing the pipeline."
        )
    return resolve


def get_project_framerate(project) -> float:
    """Get the project timeline framerate as a float."""
    fps_str = project.GetSetting("timelineFrameRate")
    try:
        return float(fps_str)
    except (TypeError, ValueError):
        print(f"  Warning: Could not read framerate (got '{fps_str}'), defaulting to 30.0")
        return 30.0


# ─── Media Import ───────────────────────────────────────────────────

def import_media(media_pool, file_path: str):
    """
    Import a single file into the media pool.
    Returns the MediaPoolItem, or None on failure.
    """
    if not os.path.exists(file_path):
        print(f"  ERROR: File not found: {file_path}")
        return None

    imported = media_pool.ImportMedia([file_path])
    if imported and len(imported) > 0:
        return imported[0]

    print(f"  ERROR: Resolve refused to import: {file_path}")
    return None


def find_clip_in_pool(folder, filename: str):
    """
    Search the media pool folder for a clip matching the given filename.
    Returns the MediaPoolItem or None.
    """
    clips = folder.GetClipList()
    if not clips:
        return None

    for clip in clips:
        clip_name = clip.GetClipProperty("File Name") or clip.GetName()
        if clip_name and os.path.basename(clip_name) == os.path.basename(filename):
            return clip

    return None


# ─── Timeline Assembly ──────────────────────────────────────────────

def seconds_to_frame(seconds: float, fps: float) -> int:
    """Convert seconds to frame number (0-based)."""
    return int(round(seconds * fps))


def build_timeline_from_edl(
    edl_plan: dict,
    source_video: str,
    cfg: dict,
    out_dir: str
) -> str:
    """
    Build a timeline in DaVinci Resolve from an EDL plan.

    Args:
        edl_plan: Validated EDL dict with keep_segments.
        source_video: Absolute path to the synced video file.
        cfg: Config dict (from config_webinar.yaml).
        out_dir: Output directory for renders.

    Returns:
        Path to expected output file.
    """
    print("\n  === Resolve Timeline Builder ===")

    # Connect
    print("  Connecting to DaVinci Resolve...")
    resolve = get_resolve()
    pm = resolve.GetProjectManager()

    # Create project
    project_name = edl_plan.get("title", "PixelSpryte_Edit").replace(" ", "_")
    project = pm.CreateProject(project_name)
    if not project:
        # May already exist — try loading it
        project = pm.LoadProject(project_name)
    if not project:
        raise RuntimeError(f"Could not create or load project: {project_name}")

    fps = get_project_framerate(project)
    print(f"  Project: {project_name} ({fps} fps)")

    # Import source video
    media_pool = project.GetMediaPool()
    root_folder = media_pool.GetRootFolder()

    # Check if already in pool
    clip = find_clip_in_pool(root_folder, source_video)
    if clip:
        print(f"  Source already in pool: {os.path.basename(source_video)}")
    else:
        print(f"  Importing: {os.path.basename(source_video)}")
        clip = import_media(media_pool, source_video)

    if not clip:
        raise RuntimeError(f"Failed to get source clip in media pool: {source_video}")

    # CRITICAL: startFrame/endFrame in clipInfo are frame indices into the
    # SOURCE CLIP, so the seconds->frames math must use the clip's fps,
    # not the project's. (24 fps project + 29.97 fps clip = cuts ~20% off.)
    clip_fps = None
    try:
        clip_fps = float(clip.GetClipProperty("FPS"))
    except (TypeError, ValueError):
        pass
    if clip_fps:
        if abs(clip_fps - fps) > 0.01:
            print(f"  Clip fps ({clip_fps}) differs from project fps ({fps})")
            # Try to conform the project timeline rate to the footage.
            # Only works while the project has no timelines yet.
            if project.SetSetting("timelineFrameRate", str(clip_fps)):
                print(f"  Project timeline rate set to {clip_fps} fps")
            else:
                print(f"  Could not change project rate (timelines exist?) — "
                      f"cuts still placed correctly using clip fps")
        fps = clip_fps
    else:
        print(f"  Warning: could not read clip fps, using project fps ({fps})")

    # Build segment list
    segments = edl_plan.get("keep_segments", [])
    if not segments:
        print("  ERROR: EDL has no keep_segments")
        return ""

    print(f"  Segments to place: {len(segments)}")

    # Create clipInfo dicts for AppendToTimeline
    clip_infos = []
    for seg in segments:
        start_sec = seg.get("keep_start", 0.0)
        end_sec = seg.get("keep_end", 0.0)

        start_frame = seconds_to_frame(start_sec, fps)
        end_frame = seconds_to_frame(end_sec, fps)

        if end_frame <= start_frame:
            print(f"  Skipping segment idx={seg.get('idx')}: zero or negative duration")
            continue

        clip_infos.append({
            "mediaPoolItem": clip,
            "startFrame": start_frame,
            "endFrame": end_frame,
        })

    if not clip_infos:
        print("  ERROR: No valid segments to place")
        return ""

    # Create timeline and place segments
    timeline_name = "PixelSpryte_Edit"
    print(f"  Creating timeline: {timeline_name}")

    # NOTE: CreateTimelineFromClips silently drops the startFrame/endFrame
    # in/out points (places only the first/full clip). AppendToTimeline is
    # the only reliable way to place trimmed ranges.
    timeline = media_pool.CreateEmptyTimeline(timeline_name)
    if not timeline:
        raise RuntimeError("Could not create timeline")

    project.SetCurrentTimeline(timeline)
    placed = media_pool.AppendToTimeline(clip_infos)
    if not placed:
        raise RuntimeError("AppendToTimeline placed nothing")

    n_items = len(timeline.GetItemListInTrack("video", 1) or [])
    print(f"  Placed {n_items}/{len(clip_infos)} segments on timeline")
    if n_items < len(clip_infos):
        print("  WARNING: some segments were not placed — check timeline")

    # Apply zoom transforms where specified
    _apply_zoom_transforms(timeline, segments, fps)

    # Add caption markers where specified
    _add_caption_markers(timeline, segments, fps)

    # Review aids: reason markers + clip colors on zoomed segments
    if (cfg.get("review") or {}).get("reason_markers", True):
        _add_reason_markers(timeline, segments)
    _set_clip_colors(timeline, segments)

    # Consistent look: apply configured LUT to every clip
    lut_path = (cfg.get("color") or {}).get("lut_path")
    if lut_path:
        _apply_lut(timeline, lut_path)

    # B-roll inserts on video track 2
    broll_specs = edl_plan.get("broll") or []
    if broll_specs and (cfg.get("edit_defaults") or {}).get("broll", "none") != "none":
        _place_broll(media_pool, timeline, broll_specs, segments, fps, cfg)

    # Subtitles: .srt sidecar remapped to the edit (+ optional Resolve AI captions)
    captions_mode = (cfg.get("edit_defaults") or {}).get("captions", "none")
    if captions_mode != "none":
        # Prefer the raw transcript (full text) over the truncated metadata
        transcript_path = os.path.join(out_dir, "transcript_raw.json")
        if not os.path.exists(transcript_path):
            transcript_path = os.path.join(out_dir, "transcript.json")
        if os.path.exists(transcript_path):
            from core.subtitles import write_edit_srt
            srt_path = os.path.join(out_dir, f"{project_name}_captions.srt")
            try:
                write_edit_srt(transcript_path, segments, srt_path)
            except Exception as e:
                print(f"  Warning: .srt generation failed: {e}")
        else:
            print("  No transcript.json in output dir — skipping .srt sidecar")
        if (cfg.get("subtitles") or {}).get("resolve_ai", False):
            create_ai_subtitles(timeline)

    # Timeline backup (OTIO) — cheap insurance for every auto-edit
    if (cfg.get("export") or {}).get("timeline_backup", True):
        _export_timeline_backup(resolve, timeline, out_dir)

    # Configure render
    output_path = _configure_render(project, out_dir, cfg)

    print(f"\n  === Timeline Ready ===")
    print(f"  Open the Edit page in Resolve to review.")
    print(f"  Use Ctrl+Z to undo any segment placements.")
    print(f"  Render from the Deliver page when satisfied.\n")

    return output_path


# ─── Zoom Transforms ───────────────────────────────────────────────

def _apply_zoom_transforms(timeline, segments: list, fps: float):
    """
    Apply ZoomX/ZoomY to timeline items that have a zoom value in the EDL.
    Zoom of 1.10 = 10% punch-in on the clip.
    """
    zoom_count = sum(1 for s in segments if s.get("zoom") and s["zoom"] != 1.0)
    if zoom_count == 0:
        return

    print(f"  Applying zoom to {zoom_count} segment(s)...")

    # Get all items on video track 1
    items = timeline.GetItemListInTrack("video", 1)
    if not items:
        print("  Warning: No items on video track 1, skipping zoom")
        return

    # Match items to segments by index order
    # (items are in timeline order, same as our clip_infos order)
    item_idx = 0
    for seg in segments:
        zoom = seg.get("zoom")
        if not zoom or zoom == 1.0:
            item_idx += 1
            continue

        if item_idx >= len(items):
            break

        item = items[item_idx]
        # Resolve ZoomX/ZoomY: 1.0 = 100%, so 1.10 = 110% punch-in
        success = item.SetProperty("ZoomX", float(zoom))
        if success:
            item.SetProperty("ZoomY", float(zoom))
            item.SetProperty("ZoomGang", True)
        else:
            print(f"  Warning: Could not set zoom on segment idx={seg.get('idx')}")

        item_idx += 1


# ─── Caption Markers ───────────────────────────────────────────────

def _add_caption_markers(timeline, segments: list, fps: float):
    """
    Add markers to timeline items that have caption_text.
    Markers show up in Resolve's timeline and can guide manual caption placement.

    Note: The Resolve scripting API doesn't support directly creating
    subtitle track entries. Markers are the best automated approach —
    you can then use Resolve's built-in subtitle tools to add text at
    each marker.
    """
    caption_count = sum(1 for s in segments if s.get("caption_text"))
    if caption_count == 0:
        return

    print(f"  Adding caption markers to {caption_count} segment(s)...")

    items = timeline.GetItemListInTrack("video", 1)
    if not items:
        return

    item_idx = 0
    for seg in segments:
        caption = seg.get("caption_text")
        if not caption:
            item_idx += 1
            continue

        if item_idx >= len(items):
            break

        item = items[item_idx]
        # Add a blue marker at frame 0 of this clip with the caption text
        item.AddMarker(
            0,          # frame offset within the clip
            "Blue",     # marker color
            caption[:50],  # marker name (truncated)
            caption,       # marker note (full text)
            1              # duration in frames
        )
        item_idx += 1

    print(f"  Markers added. Use Edit > Find Markers to review them.")


# ─── Review Aids ────────────────────────────────────────────────────

def _add_reason_markers(timeline, segments: list):
    """Green marker on each kept clip explaining why the AI kept it."""
    items = timeline.GetItemListInTrack("video", 1) or []
    count = 0
    for seg, item in zip(segments, items):
        reason = (seg.get("reason") or "").strip()
        if not reason:
            continue
        if item.AddMarker(0, "Green", reason[:50], reason, 1):
            count += 1
    if count:
        print(f"  Reason markers added to {count} clip(s)")


def _set_clip_colors(timeline, segments: list):
    """Color-code timeline clips: yellow = punch-in applied."""
    items = timeline.GetItemListInTrack("video", 1) or []
    count = 0
    for seg, item in zip(segments, items):
        if seg.get("zoom") and seg["zoom"] != 1.0:
            if item.SetClipColor("Yellow"):
                count += 1
    if count:
        print(f"  Clip colors set on {count} zoomed clip(s)")


# ─── LUT / Grade ────────────────────────────────────────────────────

def _apply_lut(timeline, lut_path: str):
    """Apply a .cube LUT to node 1 of every clip on video track 1."""
    if not os.path.exists(lut_path):
        print(f"  Warning: LUT not found, skipping: {lut_path}")
        return
    items = timeline.GetItemListInTrack("video", 1) or []
    applied = sum(1 for item in items if item.SetLUT(1, lut_path))
    print(f"  LUT applied to {applied}/{len(items)} clip(s): "
          f"{os.path.basename(lut_path)}")


# ─── B-roll Placement ───────────────────────────────────────────────

def _source_to_timeline_seconds(source_sec: float, keeps: list):
    """Map a source timestamp to edited-timeline seconds (None if cut)."""
    offset = 0.0
    for k in keeps:
        ks, ke = k.get("keep_start", 0.0), k.get("keep_end", 0.0)
        if ks <= source_sec < ke:
            return offset + (source_sec - ks)
        offset += max(0.0, ke - ks)
    return None


def _place_broll(media_pool, timeline, broll_specs: list, keeps: list,
                 timeline_fps: float, cfg: dict):
    """
    Place matching library clips on video track 2 at the timeline
    positions of the planner's B-roll suggestions.
    """
    from core.broll import load_library, find_clip

    paths_cfg = cfg.get("paths") or {}
    root = paths_cfg.get("project_root", ".")

    def _abs(p):
        return p if os.path.isabs(p) else os.path.join(root, p)

    broll_dir = _abs(paths_cfg.get("broll_dir", "assets/broll"))
    tags_csv = _abs(paths_cfg.get("tags_csv", "assets/tags.csv"))

    library = load_library(broll_dir, tags_csv)
    if not library:
        print(f"  B-roll: library empty ({broll_dir}) — skipping "
              f"{len(broll_specs)} suggestion(s)")
        return

    # Ensure V2 exists
    if timeline.GetTrackCount("video") < 2:
        if not timeline.AddTrack("video"):
            print("  B-roll: could not add video track 2 — skipping")
            return

    # recordFrame is ABSOLUTE (timeline start frame + offset)
    tl_base = timeline.GetStartFrame()
    placed = 0

    for spec in broll_specs:
        query = spec.get("query", "")
        tl_sec = _source_to_timeline_seconds(spec.get("start", 0.0), keeps)
        if tl_sec is None:
            print(f"  B-roll '{query}': moment was cut from the edit — skipped")
            continue

        clip_path = find_clip(query, library)
        if not clip_path:
            print(f"  B-roll '{query}': no matching clip in library — skipped")
            continue

        clip = find_clip_in_pool(media_pool.GetRootFolder(), clip_path)
        if not clip:
            clip = import_media(media_pool, clip_path)
        if not clip:
            continue

        # Trim length in the B-ROLL clip's own frame domain
        try:
            b_fps = float(clip.GetClipProperty("FPS"))
        except (TypeError, ValueError):
            b_fps = timeline_fps
        duration = min(spec.get("end", 0) - spec.get("start", 0), 8.0)
        end_frame = max(1, int(duration * b_fps))

        info = {
            "mediaPoolItem": clip,
            "startFrame": 0,
            "endFrame": end_frame,
            "trackIndex": 2,
            "recordFrame": tl_base + int(tl_sec * timeline_fps),
        }
        if media_pool.AppendToTimeline([info]):
            placed += 1
            print(f"  B-roll '{query}' -> {os.path.basename(clip_path)} "
                  f"at {tl_sec:.1f}s")

    print(f"  B-roll: placed {placed}/{len(broll_specs)}")


# ─── Subtitles (Resolve AI) ─────────────────────────────────────────

def create_ai_subtitles(timeline) -> bool:
    """
    Run Resolve's built-in AI captioning on the edited timeline.
    Timing is inherently correct because it transcribes the timeline
    audio post-edit. Studio only; can take a while on long timelines.
    """
    print("  Running Resolve AI captions (this can take a few minutes)...")
    try:
        if timeline.CreateSubtitlesFromAudio():
            print("  AI captions created on subtitle track")
            return True
    except TypeError:
        try:
            if timeline.CreateSubtitlesFromAudio({}):
                print("  AI captions created on subtitle track")
                return True
        except Exception as e:
            print(f"  AI captions failed: {e}")
    except Exception as e:
        print(f"  AI captions failed: {e}")
    return False


# ─── Timeline Backup ────────────────────────────────────────────────

def _export_timeline_backup(resolve, timeline, out_dir: str):
    """Export the timeline as OTIO next to the other outputs."""
    import datetime
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"timeline_backup_{stamp}.otio")
    try:
        if timeline.Export(path, resolve.EXPORT_OTIO):
            print(f"  Timeline backup: {os.path.basename(path)}")
    except Exception as e:
        print(f"  Timeline backup failed (non-fatal): {e}")


# ─── Rendering ──────────────────────────────────────────────────────

def render_and_wait(project, out_dir: str, custom_name: str,
                    poll_seconds: int = 5) -> str:
    """
    Add a render job for the current timeline, start it, and poll
    until completion. Returns the rendered file path.
    """
    import time

    os.makedirs(out_dir, exist_ok=True)

    try:
        project.SetCurrentRenderFormatAndCodec("mp4", "H264")
    except Exception:
        pass  # keep whatever format is currently selected

    project.SetRenderSettings({
        "TargetDir": out_dir,
        "CustomName": custom_name,
    })

    job = project.AddRenderJob()
    if not job:
        raise RuntimeError("Could not add render job (is a timeline open?)")

    if not project.StartRendering(job):
        raise RuntimeError("Resolve refused to start rendering")

    print(f"  Rendering '{custom_name}'...")
    last_pct = -1
    while project.IsRenderingInProgress():
        status = project.GetRenderJobStatus(job) or {}
        pct = status.get("CompletionPercentage", 0)
        if pct != last_pct:
            print(f"  Render progress: {pct}%")
            last_pct = pct
        time.sleep(poll_seconds)

    status = project.GetRenderJobStatus(job) or {}
    if status.get("JobStatus") != "Complete":
        raise RuntimeError(f"Render did not complete: {status}")

    elapsed = status.get("TimeTakenToRenderInMs", 0) / 1000
    output_path = os.path.join(out_dir, f"{custom_name}.mp4")
    print(f"  Render complete in {elapsed:.0f}s: {output_path}")
    return output_path


# ─── Render Config ──────────────────────────────────────────────────

def _configure_render(project, out_dir: str, cfg: dict) -> str:
    """Set up render settings. Does NOT start rendering."""
    os.makedirs(out_dir, exist_ok=True)

    brand = cfg.get("brand", {})
    title = brand.get("name", "PixelSpryte").replace(" ", "_")

    project.SetRenderSettings({
        "TargetDir": out_dir,
        "CustomName": f"{title}_final",
        "FormatWidth": 1920,
        "FormatHeight": 1080,
    })

    output_path = os.path.join(out_dir, f"{title}_final.mp4")
    print(f"  Render configured: {output_path}")
    print(f"  Go to Deliver page in Resolve to render.")

    return output_path


# ─── Standalone Test ────────────────────────────────────────────────

def check_resolve_connection() -> bool:
    """Quick check if Resolve is running and scriptable."""
    try:
        resolve = get_resolve()
        pm = resolve.GetProjectManager()
        return pm is not None
    except RuntimeError:
        return False


if __name__ == "__main__":
    print("Testing Resolve connection...")
    if check_resolve_connection():
        print("  Connected to DaVinci Resolve!")
        resolve = get_resolve()
        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject()
        if project:
            fps = get_project_framerate(project)
            print(f"  Current project: {project.GetName()} ({fps} fps)")
        else:
            print("  No project open.")
    else:
        print("  Could not connect. Is Resolve running?")
        print("  Check: Preferences > System > General > External scripting = Local")

