"""
PixelSpryte Webinar Pipeline — Interactive
Run: python run_webinar.py

Walks you through each step with a menu. You choose what to run.
"""

import os
import json
import yaml
import sys


# --- Helpers ---------------------------------------------------------------

def clear():
    os.system("cls" if os.name == "nt" else "clear")


def ask(prompt, options):
    """Show a numbered menu, return the chosen option key."""
    print()
    for i, (key, label) in enumerate(options, 1):
        print(f"  [{i}] {label}")
    print()
    while True:
        choice = input(f"  {prompt} ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1][0]
        print(f"  Enter 1-{len(options)}")


def file_status(path):
    """Return a status string for a file path."""
    if os.path.exists(path):
        size_mb = os.path.getsize(path) / (1024 * 1024)
        return f"FOUND ({size_mb:.1f} MB)"
    return "MISSING"


def header(title):
    print()
    print("=" * 55)
    print(f"  {title}")
    print("=" * 55)


# --- Load Config -----------------------------------------------------------

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".m4v")


def load_config(config_file="config_webinar.yaml"):
    if not os.path.exists(config_file):
        print(f"  ERROR: {config_file} not found in {os.getcwd()}")
        sys.exit(1)

    with open(config_file, "r") as f:
        cfg = yaml.safe_load(f)

    paths = cfg["paths"]
    root = paths["project_root"]

    def resolve_path(p):
        return p if os.path.isabs(p) else os.path.join(root, p)

    src = cfg.get("source_files") or {}

    # Source clips: explicit list > folder scan > legacy part1/part2 keys
    videos = []
    if src.get("video_clips"):
        videos = [resolve_path(p) for p in src["video_clips"]]
    elif src.get("video_dir"):
        vdir = resolve_path(src["video_dir"])
        if os.path.isdir(vdir):
            videos = sorted(
                os.path.join(vdir, f) for f in os.listdir(vdir)
                if f.lower().endswith(VIDEO_EXTENSIONS)
            )
    else:
        for key in ("video_part1", "video_part2"):
            if src.get(key):
                videos.append(resolve_path(src[key]))

    audio_master = resolve_path(src["audio_master"]) if src.get("audio_master") else None
    script = resolve_path(src["script_reference"]) if src.get("script_reference") else None

    out_dir = resolve_path(paths["outputs_dir"])
    synced_name = paths.get("synced_name", "briefing_synced.mp4")
    edl_name = paths.get("edl_name", "webinar_edl.json")

    cfg["_config_file"] = config_file
    cfg["_resolved"] = {
        "videos": videos,
        "audio_master": audio_master,
        "script": script,
        "output_dir": out_dir,
        "synced": os.path.join(out_dir, synced_name),
        "transcript": os.path.join(out_dir, "transcript.json"),
        "edl": os.path.join(out_dir, edl_name),
    }

    os.makedirs(out_dir, exist_ok=True)
    return cfg


# --- Steps -----------------------------------------------------------------

def step_status(cfg):
    """Show current state of all files and pipeline progress."""
    r = cfg["_resolved"]
    header("Pipeline Status")

    print(f"\n  Config: {cfg.get('_config_file', '?')}")
    print(f"  LLM:    {cfg['llm']['backend']} / {cfg['llm'].get('model_name', '?')}")
    print(f"  Style:  {cfg['edit_defaults'].get('style', 'retention')}")
    print(f"  Target: {cfg['edit_defaults']['target_minutes']} min")

    print(f"\n  INPUT FILES:")
    if r["videos"]:
        for i, v in enumerate(r["videos"], 1):
            print(f"    Clip {i:<8} : {file_status(v)}")
            print(f"                   {v}")
    else:
        print(f"    No video clips found — check source_files in the config")
    if r["audio_master"]:
        print(f"    Master Audio : {file_status(r['audio_master'])}")
        print(f"                   {r['audio_master']}")
    else:
        print(f"    Master Audio : (none — camera audio kept)")
    if r["script"]:
        print(f"    Script       : {file_status(r['script'])}")
    else:
        print(f"    Script       : (none)")

    print(f"\n  OUTPUT FILES:")
    print(f"    Synced Video : {file_status(r['synced'])}")
    print(f"    Transcript   : {file_status(r['transcript'])}")
    print(f"    EDL Plan     : {file_status(r['edl'])}")


def step_sync(cfg):
    """Step 1: Concatenate videos (+ sync external audio if configured)."""
    r = cfg["_resolved"]
    audio_cfg = cfg["audio"]
    has_master = bool(r["audio_master"])

    header("Step 1 -- Merge Clips" + (" + Audio Sync" if has_master else ""))

    # Check inputs
    videos = [v for v in r["videos"] if os.path.exists(v)]
    for i, v in enumerate(r["videos"], 1):
        print(f"  Clip {i}: {file_status(v)}  {os.path.basename(v)}")

    if has_master:
        print(f"  Master Audio: {file_status(r['audio_master'])}")

    if not videos:
        print(f"\n  No video files found. Check source_files in {cfg.get('_config_file')}")
        input("  Press Enter to return...")
        return

    if has_master and not os.path.exists(r["audio_master"]):
        print(f"\n  Master audio not found. Update {cfg.get('_config_file')}")
        input("  Press Enter to return...")
        return

    if os.path.exists(r["synced"]):
        print(f"\n  Output already exists: {r['synced']}")
        action = ask("What do you want to do?", [
            ("skip", "Keep existing file, go back to menu"),
            ("redo", "Delete and re-sync"),
        ])
        if action == "skip":
            return
        os.remove(r["synced"])

    print(f"\n  This will:")
    print(f"    1. Join {len(videos)} video clip(s) in sequence")
    sync_method = audio_cfg.get("sync_method", "waveform")
    if has_master:
        print(f"    2. Sync your external audio via {sync_method} matching")
        print(f"    3. Replace camera audio with the external track")
    else:
        print(f"    2. Keep the camera audio (no external track configured)")
    if audio_cfg.get("normalize"):
        print(f"    +  Normalize to {audio_cfg.get('target_lufs', -14)} LUFS")
    print(f"    -> Output: {r['synced']}")

    action = ask("Proceed?", [
        ("go", "Yes, run it"),
        ("cancel", "No, go back to menu"),
    ])

    if action == "cancel":
        return

    lufs = audio_cfg.get("target_lufs") if audio_cfg.get("normalize") else None

    try:
        if has_master:
            from core.audio_sync import sync_and_merge
            sync_and_merge(
                video_files=videos,
                master_audio=r["audio_master"],
                output_path=r["synced"],
                sync_method=sync_method,
                normalize_lufs=lufs
            )
        else:
            from core.audio_sync import merge_clips_only
            merge_clips_only(
                video_files=videos,
                output_path=r["synced"],
                normalize_lufs=lufs
            )
        print("\n  Merge complete!")
    except Exception as e:
        print(f"\n  ERROR: {e}")

    input("  Press Enter to continue...")


def step_transcribe(cfg):
    """Step 2: Transcribe with Whisper."""
    r = cfg["_resolved"]

    header("Step 2 -- Transcribe")

    if not os.path.exists(r["synced"]):
        print("  Synced video not found. Run Step 1 first.")
        input("  Press Enter to return...")
        return

    print(f"  Input: {r['synced']} ({file_status(r['synced'])})")

    if os.path.exists(r["transcript"]):
        with open(r["transcript"], "r") as f:
            segments = json.load(f)
        print(f"\n  Transcript already exists: {len(segments)} segments")
        if segments:
            excerpt0 = segments[0].get("transcript_excerpt", "")[:80]
            excerpt_last = segments[-1].get("transcript_excerpt", "")[:80]
            print(f'  First: "{excerpt0}..."')
            print(f'  Last:  "{excerpt_last}..."')

        action = ask("What do you want to do?", [
            ("skip", "Keep existing transcript, go back"),
            ("redo", "Delete and re-transcribe"),
        ])
        if action == "skip":
            return
        os.remove(r["transcript"])

    whisper_size = ask("Whisper model size? Larger = slower but more accurate.", [
        ("base", "Base -- fast, good enough for clean audio (Recommended)"),
        ("small", "Small -- better accuracy, 2-3x slower"),
        ("medium", "Medium -- best accuracy, 5-10x slower"),
    ])

    print(f"\n  This will:")
    print(f"    1. Run Whisper ({whisper_size}) on the synced video")
    print(f"    2. Generate word-level timestamps")
    print(f"    3. Score segments by energy + detect topics")
    print(f"    -> Output: {r['transcript']}")
    print(f"\n  This may take several minutes depending on your GPU.")

    action = ask("Proceed?", [
        ("go", "Yes, transcribe"),
        ("cancel", "No, go back"),
    ])

    if action == "cancel":
        return

    try:
        from core.asr_whisperx import transcribe_files, segments_to_metadata

        raw_segments = transcribe_files([r["synced"]], model_size=whisper_size)
        segments = segments_to_metadata(raw_segments)

        with open(r["transcript"], "w") as f:
            json.dump(segments, f, indent=2)
        # Keep the raw pass too (full text + word timestamps) for subtitles
        with open(r["transcript"].replace(".json", "_raw.json"), "w") as f:
            json.dump(raw_segments, f, indent=2)

        print(f"\n  Transcription complete: {len(segments)} segments")
    except Exception as e:
        print(f"\n  ERROR: {e}")

    input("  Press Enter to continue...")


def step_edl(cfg):
    """Step 3: Generate EDL via LLM."""
    r = cfg["_resolved"]

    header("Step 3 -- Generate EDL (Cut List)")

    if not os.path.exists(r["transcript"]):
        print("  Transcript not found. Run Step 2 first.")
        input("  Press Enter to return...")
        return

    with open(r["transcript"], "r") as f:
        segments = json.load(f)
    print(f"  Transcript: {len(segments)} segments loaded")

    script_text = None
    if r["script"] and os.path.exists(r["script"]):
        with open(r["script"], "r", encoding="utf-8") as f:
            script_text = f.read()
        print(f"  Script reference: loaded ({len(script_text)} chars)")
    else:
        print(f"  Script reference: not found (will proceed without it)")

    from core.planner_llm import check_llm_status
    backend = cfg["llm"]["backend"]
    model = cfg["llm"].get("model_name", "?")
    print(f"  LLM: {backend} / {model}")

    if check_llm_status(cfg):
        print(f"  LLM status: READY")
    else:
        print(f"  LLM status: NOT READY")
        if backend == "koboldcpp":
            print("  Start KoboldCPP, or switch to API in config_webinar.yaml")
        else:
            print("  Check your API key in config_webinar.yaml")
        print("  (Will fall back to heuristic editor if you proceed)")

    if os.path.exists(r["edl"]):
        with open(r["edl"], "r") as f:
            existing = json.load(f)
        n_keep = len(existing.get("keep_segments", []))
        n_cut = len(existing.get("cut_segments", []))
        print(f"\n  EDL already exists: {n_keep} keeps, {n_cut} cuts")

        action = ask("What do you want to do?", [
            ("skip", "Keep existing EDL, go back"),
            ("redo", "Regenerate EDL (costs one API call)"),
            ("view", "View existing EDL, then decide"),
        ])

        if action == "skip":
            return
        elif action == "view":
            _preview_edl(existing)
            action2 = ask("Regenerate?", [
                ("skip", "No, keep this EDL"),
                ("redo", "Yes, regenerate"),
            ])
            if action2 == "skip":
                return

    else:
        print(f"\n  This will:")
        print(f"    1. Send {len(segments)} transcript segments to {model}")
        if script_text:
            print(f"    2. Compare against your original script")
            print(f"    3. Generate cut/keep decisions for each segment")
        else:
            print(f"    2. Generate cut/keep decisions for each segment")
        print(f"    -> Output: {r['edl']}")

        action = ask("Proceed?", [
            ("go", "Yes, generate EDL"),
            ("cancel", "No, go back"),
        ])
        if action == "cancel":
            return

    from core.planner_llm import build_edl_with_llm
    from core.edl_schema import EDLPlan

    print("\n  Generating EDL (this may take 30-60 seconds)...")

    edl_json = build_edl_with_llm(
        segments=segments,
        target_minutes=cfg["edit_defaults"]["target_minutes"],
        structure=cfg["edit_defaults"]["keep_structure"],
        allow_zoom=cfg["edit_defaults"]["allow_zoom_ins"],
        allow_jump=cfg["edit_defaults"]["allow_jumpcuts"],
        captions=cfg["edit_defaults"]["captions"],
        cfg=cfg,
        script_reference=script_text
    )

    try:
        plan = EDLPlan.model_validate(edl_json)
        print(f"\n  Validation: PASSED")
        print(f"  {plan.summary()}")
    except Exception as e:
        print(f"\n  Validation: FAILED -- {e}")

    _preview_edl(edl_json)

    action = ask("Save this EDL?", [
        ("save", "Yes, save it"),
        ("discard", "No, discard and go back"),
    ])

    if action == "save":
        with open(r["edl"], "w") as f:
            json.dump(edl_json, f, indent=2)
        print(f"  Saved: {r['edl']}")
    else:
        print("  Discarded.")

    input("  Press Enter to continue...")


def _preview_edl(edl):
    """Show a compact preview of an EDL plan."""
    keeps = edl.get("keep_segments", [])
    cuts = edl.get("cut_segments", [])

    print(f"\n  +-- EDL Preview ----------------------------------------")
    print(f"  | Title:  {edl.get('title', '?')}")
    print(f"  | Keep:   {len(keeps)} segments")
    print(f"  | Cut:    {len(cuts)} segments")

    if keeps:
        total = sum(s.get("keep_end", 0) - s.get("keep_start", 0) for s in keeps)
        print(f"  | Duration: {total/60:.1f} min")
        print(f"  |")
        print(f"  | First 5 keeps:")
        for seg in keeps[:5]:
            start = seg.get("keep_start", 0)
            end = seg.get("keep_end", 0)
            reason = seg.get("reason", "")[:50]
            print(f"  |   {start:.1f}s -> {end:.1f}s  ({reason})")
        if len(keeps) > 5:
            print(f"  |   ... and {len(keeps) - 5} more")

    print(f"  +------------------------------------------------------")


def step_resolve(cfg):
    """Step 4: Build Resolve timeline from EDL."""
    r = cfg["_resolved"]

    header("Step 4 -- Build Resolve Timeline")

    if not os.path.exists(r["edl"]):
        print("  EDL not found. Run Step 3 first.")
        input("  Press Enter to return...")
        return

    if not os.path.exists(r["synced"]):
        print("  Synced video not found. Run Step 1 first.")
        input("  Press Enter to return...")
        return

    with open(r["edl"], "r") as f:
        edl = json.load(f)

    n_keep = len(edl.get("keep_segments", []))
    n_zoom = sum(1 for s in edl.get("keep_segments", [])
                 if s.get("zoom") and s["zoom"] != 1.0)
    n_caption = sum(1 for s in edl.get("keep_segments", [])
                    if s.get("caption_text"))

    print(f"\n  EDL:    {n_keep} segments to place")
    if n_zoom:
        print(f"  Zoom:   {n_zoom} segments with zoom transforms")
    if n_caption:
        print(f"  Captions: {n_caption} segments with caption markers")
    print(f"  Source: {os.path.basename(r['synced'])}")

    from core.resolve_driver import check_resolve_connection
    print(f"\n  Checking Resolve connection...")
    if check_resolve_connection():
        print(f"  DaVinci Resolve: CONNECTED")
    else:
        print(f"  DaVinci Resolve: NOT CONNECTED")
        print(f"  Make sure Resolve 20 is running with external scripting enabled.")
        print(f"  (Preferences > System > General > External scripting = Local)")
        input("\n  Press Enter to return...")
        return

    print(f"\n  This will:")
    print(f"    1. Create a new project in Resolve")
    print(f"    2. Import the synced video into the media pool")
    print(f"    3. Place {n_keep} segments on a timeline with in/out points")
    if n_zoom:
        print(f"    4. Apply zoom transforms to {n_zoom} segment(s)")
    if n_caption:
        step_num = 5 if n_zoom else 4
        print(f"    {step_num}. Add caption markers to {n_caption} segment(s)")
    print(f"    -> You review + Ctrl+Z to undo anything")

    action = ask("Proceed?", [
        ("go", "Yes, build the timeline"),
        ("cancel", "No, go back to menu"),
    ])

    if action == "cancel":
        return

    from core.resolve_driver import build_timeline_from_edl

    try:
        result = build_timeline_from_edl(
            edl_plan=edl,
            source_video=r["synced"],
            cfg=cfg,
            out_dir=r["output_dir"]
        )
        if result:
            print(f"\n  Timeline built! Switch to Resolve to review.")
        else:
            print(f"\n  Timeline build returned no output path.")
    except Exception as e:
        print(f"\n  ERROR: {e}")

    input("  Press Enter to continue...")


def step_review(cfg):
    """View the generated EDL in detail."""
    r = cfg["_resolved"]

    header("Review EDL")

    if not os.path.exists(r["edl"]):
        print("  No EDL found. Run Step 3 first.")
        input("  Press Enter to return...")
        return

    with open(r["edl"], "r") as f:
        edl = json.load(f)

    keeps = edl.get("keep_segments", [])
    cuts = edl.get("cut_segments", [])

    print(f"\n  Title: {edl.get('title', '?')}")
    print(f"  Captions: {edl.get('captions_style', '?')}")
    print(f"  Music: {edl.get('music', {}).get('track', 'none')}")

    if keeps:
        total = sum(s.get("keep_end", 0) - s.get("keep_start", 0) for s in keeps)
        print(f"\n  KEEP SEGMENTS ({len(keeps)} total, {total/60:.1f} min):")
        print(f"  {'#':<4} {'Start':>8} {'End':>8} {'Dur':>6}  Reason")
        print(f"  ---- -------- -------- ------  -----------------------------------")
        for seg in keeps:
            idx = seg.get("idx", "?")
            start = seg.get("keep_start", 0)
            end = seg.get("keep_end", 0)
            dur = end - start
            reason = seg.get("reason", "")[:35]
            print(f"  {idx:<4} {start:>7.1f}s {end:>7.1f}s {dur:>5.1f}s  {reason}")

    if cuts:
        print(f"\n  CUT SEGMENTS: {cuts}")

    print(f"\n  EDL file: {r['edl']}")
    print(f"  Open this JSON in a text editor for full details.")

    input("\n  Press Enter to return to menu...")


# --- Main Menu -------------------------------------------------------------

def main():
    # Usage: python run_webinar.py [config.yaml]
    #   default:      config_webinar.yaml  (Industry Briefing)
    #   family vlog:  python run_webinar.py config_vlog.yaml
    config_file = sys.argv[1] if len(sys.argv) > 1 else "config_webinar.yaml"
    cfg = load_config(config_file)

    brand = cfg.get("brand", {}).get("name", "PixelSpryte")

    while True:
        clear()
        header(f"PixelSpryte -- {brand}")

        r = cfg["_resolved"]

        s1 = "done" if os.path.exists(r["synced"]) else "    "
        s2 = "done" if os.path.exists(r["transcript"]) else "    "
        s3 = "done" if os.path.exists(r["edl"]) else "    "

        sync_label = ("join clips + replace with external audio"
                      if r["audio_master"] else "join clips (camera audio kept)")

        print(f"""
  What do you want to do?

  [1] Check status        -- see what files are ready
  [2] Merge clips   [{s1}] -- {sync_label}
  [3] Transcribe    [{s2}] -- run Whisper to get timestamps + text
  [4] Generate EDL  [{s3}] -- send to DeepSeek for cut decisions
  [5] Build timeline       -- send EDL to DaVinci Resolve
  [6] Review EDL          -- view the cut list in detail
  [7] Quit
""")

        choice = input("  Choose [1-7]: ").strip()

        if choice == "1":
            step_status(cfg)
            input("\n  Press Enter to continue...")
        elif choice == "2":
            step_sync(cfg)
        elif choice == "3":
            step_transcribe(cfg)
        elif choice == "4":
            step_edl(cfg)
        elif choice == "5":
            step_resolve(cfg)
        elif choice == "6":
            step_review(cfg)
        elif choice == "7":
            print("\n  Bye!\n")
            break


if __name__ == "__main__":
    main()

