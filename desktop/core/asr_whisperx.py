"""
PixelSpryte Transcription Module
Uses faster-whisper for GPU-accelerated speech-to-text with timestamps.
"""

from typing import List, Dict


def transcribe_files(files: List[str], model_size: str = "base") -> List[Dict]:
    """
    Transcribe audio from video files with word-level timestamps.

    Args:
        files: List of video/audio file paths.
        model_size: Whisper model size ("tiny", "base", "small", "medium").
                    "base" is good balance of speed/accuracy on GTX 1050 Ti.

    Returns:
        List of segment dicts with start, end, text, words, src_file.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "faster-whisper not installed.\n"
            "Run: pip install faster-whisper"
        )

    print(f"  Loading Whisper model: {model_size}")
    device = "cuda"
    try:
        model = WhisperModel(model_size, device=device, compute_type="int8")
    except Exception as e:
        print(f"  CUDA unavailable ({e}), using CPU")
        device = "cpu"
        model = WhisperModel(model_size, device=device, compute_type="int8")

    all_segments = []

    for file_path in files:
        print(f"  Transcribing: {file_path}")
        try:
            segments, info = model.transcribe(
                file_path,
                word_timestamps=True,
                language="en"
            )
            # faster-whisper is lazy — force execution here so CUDA
            # runtime errors (missing cuDNN etc.) surface inside this try
            segments = list(segments)
        except Exception as e:
            if device != "cuda":
                raise
            print(f"  GPU transcription failed ({e}), retrying on CPU")
            device = "cpu"
            model = WhisperModel(model_size, device=device, compute_type="int8")
            segments, info = model.transcribe(
                file_path,
                word_timestamps=True,
                language="en"
            )
            segments = list(segments)

        for segment in segments:
            all_segments.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text.strip(),
                "words": len(segment.text.split()),
                "word_timestamps": [
                    {"word": w.word, "start": w.start, "end": w.end}
                    for w in (segment.words or [])
                ],
                "src_file": file_path
            })

        print(f"    Found {len(all_segments)} segments ({info.duration:.0f}s audio)")

    return all_segments


def segments_to_metadata(transcript_segments: List[Dict]) -> List[Dict]:
    """
    Convert raw transcript segments into planner-ready metadata.
    Adds energy scoring and topic detection heuristics.
    """
    metadata = []

    for i, seg in enumerate(transcript_segments):
        text = seg.get("text", "")
        words = seg.get("words", 0)
        duration = seg["end"] - seg["start"]

        # Simple energy heuristic: words per second + keyword bonus
        wps = words / max(duration, 0.1)
        energy = min(1.0, wps / 4.0)  # Normalize: 4 wps = max energy

        # Boost energy for segments with numbers/claims
        has_numbers = any(c.isdigit() for c in text)
        if has_numbers:
            energy = min(1.0, energy + 0.15)

        # Simple topic detection
        topic = _detect_topic(text)

        metadata.append({
            "idx": i,
            "start": seg["start"],
            "end": seg["end"],
            "src_file": seg["src_file"],
            "transcript_excerpt": text[:200],
            "words": words,
            "energy": round(energy, 2),
            "topic": topic,
            "has_numbers": has_numbers,
            "visual_tags": []  # Populated by scene detection
        })

    return metadata


def _detect_topic(text: str) -> str:
    """Basic keyword-based topic detection."""
    text_lower = text.lower()

    if any(w in text_lower for w in ["welcome", "hey", "what's up", "today"]):
        return "Hook"
    elif any(w in text_lower for w in ["price", "cost", "worth", "$", "money"]):
        return "Price"
    elif any(w in text_lower for w in ["proof", "result", "earned", "made"]):
        return "Proof"
    elif any(w in text_lower for w in ["pros", "cons", "downside", "issue"]):
        return "Verdict"
    elif any(w in text_lower for w in ["subscribe", "link", "comment", "like"]):
        return "CTA"
    elif any(w in text_lower for w in ["claim", "promise", "says", "teaches"]):
        return "Claims"
    else:
        return "Main"

