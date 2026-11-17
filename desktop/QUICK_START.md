# PixelSpryte Quick Start

## Prerequisites
- Windows 10/11, Python 3.10+ (with pip)
- FFmpeg on PATH (`winget install ffmpeg`)
- DeepSeek API key (platform.deepseek.com)
- DaVinci Resolve 20 (Free or Studio; Studio needed for AI captions)
- Internet connection on render day (the LLM step is a cloud call)

## Install (recommended)

Double-click **`install.bat`**. It installs the Python packages, asks for
your API key (stored in `.env` only), creates a desktop shortcut, and adds
**PixelSpryte to Resolve's Workspace > Scripts menu** so you can launch it
from inside Resolve.

## The Control Panel

Launch from the desktop shortcut, Resolve's Scripts menu, or:
```powershell
python ui.py
```

- **Profile** — Industry Briefing / Family Vlog / YouTube; each has its own
  settings and output folder.
- **Edit settings** — LLM model, target length, style, captions, Whisper size.
- **Toggles** — jump cuts, punch-ins, B-roll inserts, audio normalize,
  Resolve AI captions.
- **Look** — pick a `.cube` LUT to apply to every clip.
- **Settings (gear)** — DeepSeek API key (masked, saved to `.env`),
  endpoint, and a Test Connection button.
- **Pipeline** — 1 Merge clips, 2 Transcribe, 3 Generate EDL,
  4 Build timeline (Resolve must be open), 5 Render MP4.

## Manual / terminal usage

```powershell
python run_webinar.py                    # Industry Briefing profile
python run_webinar.py config_vlog.yaml   # Family Vlog profile
python test_pipeline.py                  # dummy-data pipeline test, no Resolve needed
```

## How It Works

```
Raw Video (+ external Audacity audio)
    |
    v
[FFmpeg] --> concatenate clips + waveform-sync master audio
    |
    v
[faster-whisper] --> transcript + timestamps (GPU, auto-falls back to CPU)
    |
    v
[DeepSeek API] --> cut list (JSON) --> EDL plan (validated by Pydantic)
    |                                   (heuristic fallback if API fails)
    v
[DaVinci Resolve] --> timeline with cuts, zoom transforms, caption markers
```

## Configuration
- `config_webinar.yaml` — long-form webinar cleanup profile (the active one)
- `config.yaml` — short-form YouTube profile
- `llm.model_name`: `deepseek-v4-pro` (better analysis) or `deepseek-v4-flash` (cheaper/faster)

## Troubleshooting

**"API key not configured"**
- `.env` missing or `DEEPSEEK_API_KEY` not set. See step 2.

**SSL certificate errors (CERTIFICATE_VERIFY_FAILED)**
- Avast intercepts HTTPS on this machine. `truststore` (in requirements.txt)
  fixes this by using the Windows cert store. Make sure it's installed.

**"API failed after 3 attempts"**
- Check internet / firewall. The pipeline retries with backoff, then falls
  back to the heuristic editor so you still get an EDL.

**"Cannot import DaVinciResolveScript"**
- Resolve must be running, with Preferences > System > General >
  External scripting = Local. Modules path:
  `C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules`

**Whisper slow or CUDA errors**
- GPU (GTX 1050 Ti) needs cuDNN for faster-whisper. If CUDA fails, the
  pipeline automatically retries on CPU (slower but works).
