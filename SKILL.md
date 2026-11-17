---
name: pixelspryte
description: Build a vertical (9:16) reel in DaVinci Resolve from a JSON brief and verify the render. Use when asked to create, re-cut, caption, or re-render a short-form video that must end up as an editable Resolve timeline plus a checked MP4.
---

# PixelSpryte

PixelSpryte turns one `brief.json` into an editable Resolve timeline and a
verified MP4. The interface is a file contract, not an API: write the brief,
run one command, read `manifest.json`.

## The loop

1. Copy the closest existing brief, e.g. `reels/demo/brief.json` or
   `reels/demo/brief.json`. Do not invent a schema from scratch.
2. Edit `slug`, `canvas`, `footage`, `overlays`, `captions`, `audio`.
3. `py -3.12 build_reel.py reels/<slug>/brief.json`
4. Read `out/<slug>/manifest.json`. `checks.ok` must be `true`.
5. If it failed, the `problems` array names what is wrong (duration, frames,
   audio). Fix the brief, not the manifest.
6. Human review still required: caption clearance, mouth sync, claim wording.
   Never describe a local review master as approved or published.

## Cost control

- `--fal-dry-run` writes `fal_plan.json` with an estimate and spends nothing.
  Run it before any brief that adds `generate` entries.
- `--generate-only` pays for assets once, then `"reuse": true` keeps them.
- `budget.limit_usd` is enforced before every POST. If the estimate is near the
  cap, cut shots before raising the cap.

## Hard rules (each one is a real failure that was hit)

- Python 3.12 only. 3.11 segfaults importing `fusionscript.dll`.
- Resolve must be running and have external scripting enabled
  (Preferences > System > General). `doctor.py` reports both.
- Every media file must live under `out/<slug>/` next to the `.otio`. The
  builder stages and hardlinks them; do not point a clip at `C:\...\assets`.
- Never set `recordFrame: 0`. Resolve timelines start at 01:00:00:00.
- Overlay and caption stills use letter suffixes (`cap_a_`, `card_a_`), not
  `00_x.png`; numbered names break the OTIO importer.
- Captions must be burned as overlay cards if they have to be in the render.
  A native subtitle track does not burn by default.

## What "done" means

`manifest.json` exists, `checks.ok` is true, `review.html` renders with a
contact sheet, and `COST-REPORT.md` matches the jobs actually submitted.
Anything less is a draft, not a deliverable.
