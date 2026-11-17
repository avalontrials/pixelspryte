"""
PixelSpryte Audio Sync Module
Concatenates sequential video clips and replaces camera audio with
an external master track (e.g. Audacity export).

Requirements:
  - FFmpeg installed and on PATH (https://ffmpeg.org/download.html)
  - numpy (pip install numpy)
  - scipy (pip install scipy)  [only for waveform sync mode]
"""

import os
import subprocess
import tempfile
import json
from typing import List, Optional


def check_ffmpeg() -> bool:
    """Verify FFmpeg is installed and accessible."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def get_duration(filepath: str) -> float:
    """Get media file duration in seconds via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", filepath],
        capture_output=True, text=True, timeout=30
    )
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


def get_video_specs(filepath: str) -> dict:
    """Get resolution / fps / codec of the first video stream."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-select_streams", "v:0", "-show_streams", filepath],
        capture_output=True, text=True, timeout=30
    )
    stream = json.loads(result.stdout)["streams"][0]
    num, den = stream["r_frame_rate"].split("/")
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": float(num) / float(den),
        "codec": stream["codec_name"],
    }


def concatenate_videos_reencode(video_files: List[str], output_path: str,
                                target: dict) -> str:
    """
    Concatenate clips with mismatched specs by re-encoding.
    Each clip is scaled to fit the target frame (letterboxed if the
    aspect ratio differs, e.g. portrait phone clips) and conformed to
    the target fps, then joined with the concat filter.
    """
    w, h, fps = target["width"], target["height"], target["fps"]
    print(f"  Re-encoding {len(video_files)} clips to {w}x{h} @ {fps:.2f} fps...")

    cmd = ["ffmpeg", "-y"]
    for vf in video_files:
        cmd += ["-i", vf]

    filters = []
    for i in range(len(video_files)):
        filters.append(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v{i}];"
            f"[{i}:a]aresample=48000[a{i}]"
        )
    inputs = "".join(f"[v{i}][a{i}]" for i in range(len(video_files)))
    filters.append(f"{inputs}concat=n={len(video_files)}:v=1:a=1[outv][outa]")

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        output_path
    ]

    subprocess.run(cmd, check=True, capture_output=True)
    dur = get_duration(output_path)
    print(f"  Concatenated: {dur/60:.1f} min ({output_path})")
    return output_path


def concatenate_videos(video_files: List[str], output_path: str) -> str:
    """
    Concatenate sequential video files.
    Uses fast stream-copy when all clips share the same specs; falls
    back to a re-encode when resolutions/frame rates differ (common
    with mixed phone footage).

    Args:
        video_files: List of video file paths in order.
        output_path: Where to write the concatenated video.

    Returns:
        Path to concatenated video.
    """
    if len(video_files) > 1:
        specs = [get_video_specs(vf) for vf in video_files]
        first = specs[0]
        uniform = all(
            s["width"] == first["width"] and s["height"] == first["height"]
            and abs(s["fps"] - first["fps"]) < 0.01
            and s["codec"] == first["codec"]
            for s in specs
        )
        if not uniform:
            print("  Clips have mixed resolution/fps — re-encode required")
            return concatenate_videos_reencode(video_files, output_path, first)

    if len(video_files) == 1:
        # Nothing to concatenate — just copy
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_files[0],
             "-c", "copy", output_path],
            check=True, capture_output=True
        )
        return output_path

    # Build concat list file
    list_path = output_path + ".concat.txt"
    with open(list_path, "w") as f:
        for vf in video_files:
            # FFmpeg concat requires forward slashes or escaped backslashes
            safe_path = vf.replace("\\", "/")
            f.write(f"file '{safe_path}'\n")

    print(f"  Concatenating {len(video_files)} clips...")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", list_path, "-c", "copy", output_path],
        check=True, capture_output=True
    )

    os.remove(list_path)

    dur = get_duration(output_path)
    print(f"  Concatenated: {dur/60:.1f} min ({output_path})")
    return output_path


def extract_audio(video_path: str, output_wav: str) -> str:
    """Extract audio track from video as WAV for analysis."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path,
         "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
         output_wav],
        check=True, capture_output=True
    )
    return output_wav


def find_audio_offset(camera_wav: str, master_wav: str) -> float:
    """
    Find the time offset between camera audio and master audio
    using cross-correlation. Returns offset in seconds.

    Positive offset means master audio starts AFTER camera audio.
    """
    import numpy as np
    from scipy.io import wavfile
    from scipy.signal import correlate

    print("  Computing audio sync offset via cross-correlation...")

    # Read both audio files
    rate_cam, cam_data = wavfile.read(camera_wav)
    rate_mst, mst_data = wavfile.read(master_wav)

    # Use same sample rate (should both be 16kHz from extract_audio)
    if rate_cam != rate_mst:
        raise ValueError(
            f"Sample rate mismatch: camera={rate_cam}, master={rate_mst}. "
            "Both should be 16kHz mono from extract_audio()."
        )

    # Normalize to float
    cam = cam_data.astype(np.float32) / 32768.0
    mst = mst_data.astype(np.float32) / 32768.0

    # Use first 60 seconds for correlation (faster, usually enough)
    chunk = min(rate_cam * 60, len(cam), len(mst))
    cam_chunk = cam[:chunk]
    mst_chunk = mst[:chunk]

    # Cross-correlate
    corr = correlate(cam_chunk, mst_chunk, mode="full")
    lag = np.argmax(corr) - (len(mst_chunk) - 1)
    offset_sec = lag / rate_cam

    print(f"  Sync offset: {offset_sec:+.3f} seconds")
    return offset_sec


def replace_audio(
    video_path: str,
    master_audio_path: str,
    output_path: str,
    offset_sec: float = 0.0,
    normalize_lufs: Optional[float] = None
) -> str:
    """
    Replace the video's audio track with the master audio.

    Args:
        video_path: Video file (with camera audio to discard).
        master_audio_path: External audio file (WAV/MP3/etc).
        output_path: Where to write the final video.
        offset_sec: Offset to apply to master audio.
                    Positive = master starts later, pad with silence.
                    Negative = trim start of master audio.
        normalize_lufs: If set, normalize audio to this LUFS target.

    Returns:
        Path to output video.
    """
    print(f"  Replacing audio (offset: {offset_sec:+.3f}s)...")

    # Build FFmpeg filter for offset
    if offset_sec > 0:
        # Master starts late — pad with silence
        audio_filter = f"adelay={int(offset_sec * 1000)}|{int(offset_sec * 1000)}"
    elif offset_sec < 0:
        # Master starts early — trim the beginning
        audio_filter = f"atrim=start={abs(offset_sec)},asetpts=PTS-STARTPTS"
    else:
        audio_filter = "anull"

    # Add loudness normalization if requested
    if normalize_lufs is not None:
        audio_filter += f",loudnorm=I={normalize_lufs}:TP=-1.5:LRA=11"

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", master_audio_path,
        "-map", "0:v",           # Video from first input
        "-map", "1:a",           # Audio from second input (master)
        "-af", audio_filter,
        "-c:v", "copy",          # Don't re-encode video
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",             # End when shortest stream ends
        output_path
    ]

    subprocess.run(cmd, check=True, capture_output=True)

    dur = get_duration(output_path)
    print(f"  Output: {dur/60:.1f} min ({output_path})")
    return output_path


def merge_clips_only(
    video_files: List[str],
    output_path: str,
    normalize_lufs: Optional[float] = None
) -> str:
    """
    Concatenate clips keeping the camera audio (no external master track).
    Used for vlogs and any footage without a separate audio recording.
    """
    if not check_ffmpeg():
        raise RuntimeError(
            "FFmpeg not found on PATH.\n"
            "Install from https://ffmpeg.org/download.html\n"
            "Or: winget install ffmpeg"
        )

    for f in video_files:
        if not os.path.exists(f):
            raise FileNotFoundError(f"Input file not found: {f}")

    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)

    print("\n  === Clip Merge (camera audio kept) ===")

    if normalize_lufs is None:
        concatenate_videos(video_files, output_path)
    else:
        concat_path = os.path.join(output_dir, "_concat_temp.mp4")
        concatenate_videos(video_files, concat_path)
        print(f"  Normalizing audio to {normalize_lufs} LUFS...")
        subprocess.run(
            ["ffmpeg", "-y", "-i", concat_path,
             "-c:v", "copy",
             "-af", f"loudnorm=I={normalize_lufs}:TP=-1.5:LRA=11",
             "-c:a", "aac", "-b:a", "192k",
             output_path],
            check=True, capture_output=True
        )
        os.remove(concat_path)

    dur = get_duration(output_path)
    print(f"  Merged: {dur/60:.1f} min ({output_path})")
    print("  === Merge Complete ===\n")
    return output_path


def sync_and_merge(
    video_files: List[str],
    master_audio: str,
    output_path: str,
    sync_method: str = "waveform",
    manual_offset: float = 0.0,
    normalize_lufs: Optional[float] = -14.0
) -> str:
    """
    Full pipeline: concatenate videos, sync external audio, output clean file.

    Args:
        video_files: List of video files in chronological order.
        master_audio: Path to external audio (Audacity export).
        output_path: Final output path.
        sync_method: "waveform" (auto cross-correlation) or "manual".
        manual_offset: Offset in seconds (only used if sync_method="manual").
        normalize_lufs: Loudness target. None to skip normalization.

    Returns:
        Path to final merged video.
    """
    if not check_ffmpeg():
        raise RuntimeError(
            "FFmpeg not found on PATH.\n"
            "Install from https://ffmpeg.org/download.html\n"
            "Or: winget install ffmpeg"
        )

    print("\n  === Audio Sync Pipeline ===")

    # Verify all inputs exist
    all_inputs = video_files + [master_audio]
    for f in all_inputs:
        if not os.path.exists(f):
            raise FileNotFoundError(f"Input file not found: {f}")

    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: Concatenate videos
    concat_path = os.path.join(output_dir, "_concat_temp.mp4")
    concatenate_videos(video_files, concat_path)

    # Step 2: Determine audio offset
    if sync_method == "waveform":
        # Extract camera audio for cross-correlation
        cam_wav = os.path.join(output_dir, "_cam_audio_temp.wav")
        mst_wav = os.path.join(output_dir, "_mst_audio_temp.wav")

        extract_audio(concat_path, cam_wav)
        extract_audio(master_audio, mst_wav)

        offset = find_audio_offset(cam_wav, mst_wav)

        # Clean up temp WAVs
        os.remove(cam_wav)
        os.remove(mst_wav)
    else:
        offset = manual_offset
        print(f"  Using manual offset: {offset:+.3f}s")

    # Step 3: Replace audio
    replace_audio(concat_path, master_audio, output_path,
                  offset_sec=offset, normalize_lufs=normalize_lufs)

    # Clean up concat temp
    os.remove(concat_path)

    print("  === Audio Sync Complete ===\n")
    return output_path

