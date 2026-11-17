"""
PixelSpryte Pipeline Test
Tests the full pipeline with dummy data.
Run with: python test_pipeline.py
"""

import os
import json
import yaml
from core.planner_llm import build_edl_with_llm, check_llm_status
from core.edl_schema import EDLPlan


def test_pipeline(config_file="config.yaml"):
    print("=" * 50)
    print(" PixelSpryte Pipeline Test")
    print(f" Config: {config_file}")
    print("=" * 50)

    # Load config
    print(f"\n[1/4] Loading {config_file}...")
    with open(config_file, "r") as f:
        cfg = yaml.safe_load(f)
    print(f"  Brand: {cfg['brand']['name']}")
    print(f"  Target: {cfg['edit_defaults']['target_minutes']} minutes")
    print(f"  LLM: {cfg['llm']['backend']} / {cfg['llm'].get('model_name', 'default')}")

    # Check LLM backend
    print(f"\n[2/4] Checking LLM backend ({cfg['llm']['backend']})...")
    if check_llm_status(cfg):
        print("  LLM backend is ready")
    else:
        backend = cfg["llm"]["backend"]
        if backend == "koboldcpp":
            print("  WARNING: KoboldCPP not reachable at localhost:5001")
            print("  Launch KoboldCPP first, or test will use fallback heuristics")
        else:
            print("  WARNING: API key not configured. Set llm.api_key in config.")

    # Create dummy segments (simulating Whisper + SceneDetect output)
    print("\n[3/4] Preparing test segments...")
    dummy_segments = [
        {
            "idx": 0,
            "start": 0.0,
            "end": 42.5,
            "src_file": "raw_footage.mp4",
            "transcript_excerpt": "What's up everyone, today I spent $997 on this course and I'm going to tell you exactly whether it's worth it. I tested every single module over 6 weeks.",
            "energy": 0.88,
            "topic": "Hook",
            "has_numbers": True,
            "words": 38,
            "visual_tags": ["talking_head"]
        },
        {
            "idx": 1,
            "start": 42.5,
            "end": 95.0,
            "src_file": "raw_footage.mp4",
            "transcript_excerpt": "The first thing they teach you is how to find winning products using their proprietary research tool. Let me show you the dashboard right now.",
            "energy": 0.75,
            "topic": "Claims",
            "has_numbers": False,
            "words": 30,
            "visual_tags": ["talking_head", "screen_recording"]
        },
        {
            "idx": 2,
            "start": 95.0,
            "end": 180.0,
            "src_file": "raw_footage.mp4",
            "transcript_excerpt": "So here's the actual results. In my first month I made $2,400 using just module 3. The ROI calculator they provide actually works.",
            "energy": 0.92,
            "topic": "Proof",
            "has_numbers": True,
            "words": 32,
            "visual_tags": ["screen_recording", "charts"]
        },
        {
            "idx": 3,
            "start": 180.0,
            "end": 250.0,
            "src_file": "raw_footage.mp4",
            "transcript_excerpt": "Um, so yeah, I was thinking about, like, um, the whole process and honestly it was, um, it was interesting to see how things worked out over time.",
            "energy": 0.25,
            "topic": "Main",
            "has_numbers": False,
            "words": 33,
            "visual_tags": ["talking_head"]
        },
        {
            "idx": 4,
            "start": 250.0,
            "end": 320.0,
            "src_file": "raw_footage.mp4",
            "transcript_excerpt": "Now here's where it gets interesting. Module 5 teaches you scaling, and within 3 months my store was doing $8,000 per month consistently.",
            "energy": 0.90,
            "topic": "Proof",
            "has_numbers": True,
            "words": 29,
            "visual_tags": ["screen_recording", "charts"]
        },
        {
            "idx": 5,
            "start": 320.0,
            "end": 395.0,
            "src_file": "raw_footage.mp4",
            "transcript_excerpt": "The biggest downside? The Facebook ads module is outdated. They're still teaching strategies from 2022 that don't work with the new algorithm.",
            "energy": 0.70,
            "topic": "Verdict",
            "has_numbers": False,
            "words": 28,
            "visual_tags": ["talking_head"]
        },
        {
            "idx": 6,
            "start": 395.0,
            "end": 450.0,
            "src_file": "raw_footage.mp4",
            "transcript_excerpt": "At $997, is it worth it? If you're a complete beginner, absolutely yes. If you already have a store doing $5k plus, probably skip it.",
            "energy": 0.82,
            "topic": "Price",
            "has_numbers": True,
            "words": 31,
            "visual_tags": ["talking_head"]
        },
        {
            "idx": 7,
            "start": 450.0,
            "end": 490.0,
            "src_file": "raw_footage.mp4",
            "transcript_excerpt": "Link in the description if you want to check it out. Drop a comment if you have questions. Subscribe for more honest reviews.",
            "energy": 0.65,
            "topic": "CTA",
            "has_numbers": False,
            "words": 24,
            "visual_tags": ["talking_head"]
        }
    ]

    print(f"  Created {len(dummy_segments)} test segments")
    total_raw = sum(s["end"] - s["start"] for s in dummy_segments)
    print(f"  Total raw duration: {total_raw/60:.1f} min")

    # Generate EDL
    print("\n[4/4] Generating EDL with LLM...")
    edl_json = build_edl_with_llm(
        segments=dummy_segments,
        target_minutes=cfg["edit_defaults"]["target_minutes"],
        structure=cfg["edit_defaults"]["keep_structure"],
        allow_zoom=cfg["edit_defaults"]["allow_zoom_ins"],
        allow_jump=cfg["edit_defaults"]["allow_jumpcuts"],
        captions=cfg["edit_defaults"]["captions"],
        cfg=cfg
    )

    # Validate with Pydantic
    print("\n--- Results ---")
    try:
        plan = EDLPlan.model_validate(edl_json)
        print(f"  Validation: PASSED")
        print(f"  {plan.summary()}")
    except Exception as e:
        print(f"  Validation: FAILED - {e}")
        print(f"  Raw JSON keys: {list(edl_json.keys())}")
        return

    # Save EDL
    os.makedirs("outputs/test", exist_ok=True)
    output_path = "outputs/test/edl_plan.json"
    with open(output_path, "w") as f:
        json.dump(edl_json, f, indent=2)
    print(f"\n  Saved: {output_path}")

    # Summary
    print("\n" + "=" * 50)
    print(" Test complete!")
    print(f" EDL has {len(plan.keep_segments)} segments to keep")
    if plan.keep_segments:
        print(f" Total kept duration: {plan.total_duration()/60:.1f} min")
    print("=" * 50)


if __name__ == "__main__":
    import sys
    # Usage: python test_pipeline.py [config_file]
    # Default: config.yaml
    # Webinar: python test_pipeline.py config_webinar.yaml
    config_file = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    test_pipeline(config_file=config_file)

