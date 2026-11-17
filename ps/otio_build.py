"""Build an OTIO timeline from a reel brief.

OTIO is the spine: it carries exact source ranges, multiple video tracks and
audio into Resolve through ImportTimelineFromFile. Track order in the stack
maps 1:1 to Resolve track numbers (first track -> V1, bottom).

Layer order is explicit and matters:
    V1 footage -> V2..Vn b-roll -> captions -> graphics (top)

Putting captions below b-roll would hide them; putting graphics below captions
would hide the cards. The builder owns that order so a brief cannot get it wrong.
"""
from __future__ import annotations

import os

import opentimelineio as otio


def _ref(path: str) -> otio.schema.ExternalReference:
    return otio.schema.ExternalReference(
        target_url="file:///" + os.path.abspath(path).replace("\\", "/")
    )


def _clip(path: str, fps: int, in_s: float = 0.0, out_s: float | None = None,
          name: str | None = None):
    start = otio.opentime.RationalTime(round(in_s * fps), fps)
    if out_s is None:
        dur = otio.opentime.RationalTime(0, fps)  # caller must set
    else:
        dur = otio.opentime.RationalTime(round((out_s - in_s) * fps), fps)
    return otio.schema.Clip(
        name=name or os.path.basename(path),
        media_reference=_ref(path),
        source_range=otio.opentime.TimeRange(start_time=start, duration=dur),
    )


def _gap(frames: int, fps: int):
    return otio.schema.Gap(source_range=otio.opentime.TimeRange(
        otio.opentime.RationalTime(0, fps),
        otio.opentime.RationalTime(frames, fps)))


def _timed_track(name: str, clips: list[dict], fps: int) -> otio.schema.Track:
    """A track whose clips may start at arbitrary `at` offsets."""
    track = otio.schema.Track(name=name, kind=otio.schema.TrackKind.Video)
    cursor = 0
    for clip in sorted(clips, key=lambda c: c.get("at", 0.0)):
        at_frames = round(float(clip.get("at", 0.0)) * fps)
        if at_frames > cursor:
            track.append(_gap(at_frames - cursor, fps))
        c = _clip(clip["path"], fps, float(clip.get("in", 0.0)), clip.get("out"))
        if c.source_range.duration.value == 0:
            raise ValueError(f"clip needs an 'out': {clip['path']}")
        track.append(c)
        cursor = at_frames + c.source_range.duration.value
    return track


def _sequential_track(name: str, clips: list[dict], fps: int) -> otio.schema.Track:
    track = otio.schema.Track(name=name, kind=otio.schema.TrackKind.Video)
    for clip in clips:
        c = _clip(clip["path"], fps, float(clip.get("in", 0.0)), clip.get("out"))
        if c.source_range.duration.value == 0:
            raise ValueError(f"footage clip needs an 'out': {clip['path']}")
        track.append(c)
    return track


def _overlay_track(name: str, cards: list[dict], fps: int) -> otio.schema.Track:
    track = otio.schema.Track(name=name, kind=otio.schema.TrackKind.Video)
    cursor = 0
    for card in sorted(cards, key=lambda c: c["at"]):
        at_frames = round(float(card["at"]) * fps)
        dur_frames = round(float(card.get("dur", 2.5)) * fps)
        if at_frames > cursor:
            track.append(_gap(at_frames - cursor, fps))
        c = otio.schema.Clip(
            name=os.path.basename(card["path"]),
            media_reference=_ref(card["path"]),
            source_range=otio.opentime.TimeRange(
                otio.opentime.RationalTime(0, fps),
                otio.opentime.RationalTime(dur_frames, fps)),
        )
        track.append(c)
        cursor = at_frames + dur_frames
    return track


def _audio_track(name: str, clips: list[dict], fps: int) -> otio.schema.Track:
    track = otio.schema.Track(name=name, kind=otio.schema.TrackKind.Audio)
    cursor = 0
    for clip in sorted(clips, key=lambda c: c.get("at", 0.0)):
        at_frames = round(float(clip.get("at", 0.0)) * fps)
        if at_frames > cursor:
            track.append(_gap(at_frames - cursor, fps))
        c = _clip(clip["path"], fps, float(clip.get("in", 0.0)), clip.get("out"))
        if c.source_range.duration.value == 0:
            raise ValueError(f"audio clip needs an 'out': {clip['path']}")
        track.append(c)
        cursor = at_frames + c.source_range.duration.value
    return track


def build(spec: dict, out_path: str) -> str:
    """spec: {canvas:{w,h,fps}, footage:[...], video_layers:[...],
              caption_layer:{clips:[...]}, overlays:[...], audio_tracks:[...]}"""
    fps = int(spec["canvas"]["fps"])
    timeline = otio.schema.Timeline(name=spec.get("name", "reel"))
    timeline.global_start_time = otio.opentime.RationalTime(0, fps)

    tracks: list[otio.schema.Track] = []
    tracks.append(_sequential_track("V1-footage", spec.get("footage", []), fps))

    for idx, layer in enumerate(spec.get("video_layers", []), start=2):
        tracks.append(_timed_track(layer.get("name", f"V{idx}-broll"),
                                   layer.get("clips", []), fps))

    caption_layer = spec.get("caption_layer")
    if caption_layer and caption_layer.get("clips"):
        tracks.append(_timed_track("V-captions", caption_layer["clips"], fps))

    overlays = spec.get("overlays", [])
    if overlays:
        tracks.append(_overlay_track("V-graphics", overlays, fps))

    audio_tracks = spec.get("audio_tracks")
    if audio_tracks:
        for a in audio_tracks:
            tracks.append(_audio_track(a.get("name", "A"), a.get("clips", []), fps))
    else:
        audio_spec = spec.get("audio")
        if audio_spec is None:
            audio_spec = [{"path": c["path"], "in": c.get("in", 0.0), "out": c.get("out")}
                          for c in spec.get("footage", [])]
        tracks.append(_audio_track("A1", audio_spec, fps))

    for t in tracks:
        timeline.tracks.append(t)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    otio.adapters.write_to_file(timeline, out_path)
    return out_path


def total_seconds(spec: dict) -> float:
    """Longest visible/audible point across every track, not just V1."""
    end = 0.0
    cursor = 0.0
    for c in spec.get("footage", []):
        cursor += float(c.get("out", 0.0)) - float(c.get("in", 0.0))
        end = max(end, cursor)
    for layer in spec.get("video_layers", []):
        for c in layer.get("clips", []):
            end = max(end, float(c.get("at", 0.0)) + float(c.get("out", 0.0)) - float(c.get("in", 0.0)))
    caption = spec.get("caption_layer") or {}
    for c in caption.get("clips", []):
        end = max(end, float(c.get("at", 0.0)) + float(c.get("out", 0.0)) - float(c.get("in", 0.0)))
    for c in spec.get("overlays", []):
        end = max(end, float(c.get("at", 0.0)) + float(c.get("dur", 0.0)))
    for a in spec.get("audio_tracks", []):
        for c in a.get("clips", []):
            end = max(end, float(c.get("at", 0.0)) + float(c.get("out", 0.0)) - float(c.get("in", 0.0)))
    if spec.get("audio"):
        for c in spec["audio"]:
            end = max(end, float(c.get("at", 0.0)) + float(c.get("out", 0.0)) - float(c.get("in", 0.0)))
    return end
