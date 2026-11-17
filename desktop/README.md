# PixelSpryte Control Panel (original app)

This is the app that gave PixelSpryte its name: a dark, Resolve-inspired desktop control
panel that runs the whole edit as five steps. It was built in July 2026, lost with the old
laptop's `C:\PixelSpryte`, and recovered from the Codex/Claude session transfer in November
2026. The files here are reconstructed from that session's write/edit history and compile
and import cleanly, but they have **not** been run end-to-end on this machine yet.

## What it does

```
footage ──> merge clips ──> transcribe (Whisper) ──> LLM edit plan (EDL)
        ──> assemble timeline in Resolve ──> render
```

| Control | Options |
|---|---|
| Profile | **Industry Briefing** (long-form, styled captions, no b-roll), **Family Vlog** (gentle cleanup, SRT sidecar), **YouTube** |
| Style | `vlog`, `retention` |
| Captions | `none`, `srt`, `styled` |
| Model | `deepseek-v4-pro`, `deepseek-v4-flash` |
| Whisper | `base`, `small`, `medium` |
| Per-run toggles | B-roll, LUT, loudness target, dead-air and filler removal |

Each step writes its own artefact (`merged.mp4`, `transcript.json`, `edl_plan.json`, the
Resolve timeline, the render), so a failure at step 3 does not throw away steps 1 and 2.
A console pane streams the pipeline output; Settings holds the LLM endpoint and key.

## Run it

```powershell
cd desktop
py -3.12 -m pip install -r requirements.txt    # heavy: opencv, faster-whisper, scipy
py -3.12 ui.py
```

Before the first run, open the profile you want and fix `paths.project_root` (it still says
`C:\PixelSpryte`) plus `paths.inputs_dir`. Then drop clips into that folder, named in
playback order, and press **Merge clips**.

Resolve must be open with *Preferences → System → General → External scripting using: Local*.

## Two generations, one name

| | Gen 1 — this app | Gen 2 — the repo root |
|---|---|---|
| Input | a folder of footage + a YAML profile | a `brief.json` |
| Edit decision | LLM plans an EDL from the transcript | the brief states the cut |
| Assembly | Resolve scripting, clip by clip | OTIO import, then Resolve |
| Output | render preset, plus optional SRT | verified MP4, OTIO, manifest, review page |
| Formats | three content profiles | one canvas per brief (9:16) |
| Cost | LLM tokens + your time | ~$1–8 of fal.ai generation, budget-capped |

Gen 1 answers "edit this footage for me". Gen 2 answers "build this exact reel, and prove
it". The merge worth building is Gen 1's panel in front of Gen 2's engine — see
`../PARITY-PLAN.md`.

## Known gaps

- Configs still carry the old machine's absolute paths (`C:\PixelSpryte`, `D:\Davinci`).
- `install.bat` and `resolve_menu_launcher.py` assume the old `C:\PixelSpryte` layout.
- B-roll needs a tagged clip library (`core/broll.py` reads a CSV).
- Transcription needs `faster-whisper`; GPU is strongly recommended.
- No tests were recovered for the UI itself; `test_pipeline.py` covers the pipeline stages.

## Provenance

Recovered from
`Downloads\Transfer from laptops\Codex-Transfer\.claude\projects\…\ca8c3e68-….jsonl`
(the July 15, 2026 session), by replaying every Write/Edit against the Read snapshots.
Unrecoverable: `main.py`, the PyQt mockups mentioned in the session summary, and anything
written after the last captured edit.
