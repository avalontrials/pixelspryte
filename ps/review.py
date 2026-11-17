"""Human-review artifacts: a filmstrip and a self-contained HTML page."""
from __future__ import annotations

import html
import json
import os

from PIL import Image, ImageDraw

from . import verify


def filmstrip(video: str, out_dir: str, count: int = 8) -> list[str]:
    """Extract evenly spaced frames and return their paths."""
    os.makedirs(out_dir, exist_ok=True)
    info = verify.ffprobe(video)
    dur = max(0.1, info["duration"])
    paths = []
    for i in range(count):
        t = dur * (i + 0.5) / count
        p = os.path.join(out_dir, f"frame_{i:02d}_{t:.2f}s.png")
        verify.frame(video, t, p)
        paths.append(p)
    return paths


def contact_sheet(frames: list[str], out_path: str, cols: int = 4,
                  thumb_w: int = 270) -> str:
    thumbs = []
    for f in frames:
        im = Image.open(f).convert("RGB")
        im.thumbnail((thumb_w, int(thumb_w * 16 / 9)))
        canvas = Image.new("RGB", (thumb_w + 20, thumb_w * 16 // 9 + 52), (245, 242, 234))
        canvas.paste(im, (10, 8))
        d = ImageDraw.Draw(canvas)
        label = os.path.basename(f).replace("frame_", "").replace(".png", "")
        d.text((10, canvas.height - 34), label, fill=(30, 38, 52))
        thumbs.append(canvas)
    if not thumbs:
        raise ValueError("no frames")
    cols = min(cols, len(thumbs))
    rows = (len(thumbs) + cols - 1) // cols
    w, h = thumbs[0].size
    sheet = Image.new("RGB", (cols * w, rows * h), (255, 255, 255))
    for i, t in enumerate(thumbs):
        sheet.paste(t, ((i % cols) * w, (i // cols) * h))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    sheet.save(out_path, quality=90)
    return out_path


def write_report(out_dir: str, manifest: dict, frames: list[str],
                 sheet: str, brief: dict) -> str:
    rel_video = os.path.basename(manifest.get("output", {}).get("path", ""))
    rel_sheet = os.path.relpath(sheet, out_dir).replace("\\", "/")
    fal = manifest.get("fal", {})
    jobs = fal.get("jobs", [])
    session_spend = fal.get("session_estimated_usd", fal.get("spend_usd", 0))
    spend_label = "Session estimate" if "session_estimated_usd" in fal else "This build"
    licenses = manifest.get("broll", [])
    checks = manifest.get("checks", {})
    audio = manifest.get("audio", {})

    job_rows = "".join(
        f"<tr><td>{html.escape(str(j.get('id')))}</td><td>{html.escape(str(j.get('model')))}</td>"
        f"<td>{j.get('est_usd', 0):.3f}</td><td>{html.escape(str(j.get('request_id', ''))[:18])}</td></tr>"
        for j in jobs
    )
    lic_rows = "".join(
        f"<tr><td>{html.escape(str(c.get('id')))}</td>"
        f"<td><a href=\"{html.escape(str(c.get('source_page', '')))}\">{html.escape(str(c.get('license', '')))}</a></td>"
        f"<td>{c.get('duration', '')}s {c.get('width', '')}x{c.get('height', '')}</td></tr>"
        for c in licenses
    )
    frame_tags = "".join(
        f"<figure><img src=\"{html.escape(os.path.relpath(f, out_dir).replace(chr(92), '/'))}\" loading=\"lazy\">"
        f"<figcaption>{html.escape(os.path.basename(f))}</figcaption></figure>"
        for f in frames
    )
    problems = checks.get("problems") or []
    status = "PASS" if checks.get("ok") else "FAIL"
    status_color = "#2f6f55" if checks.get("ok") else "#a23b2d"

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PixelSpryte review — {html.escape(str(manifest.get('slug')))}</title>
<style>
 body{{margin:0;background:#f4f1e8;color:#18202e;font:16px/1.5 Georgia,serif}}
 main{{max-width:1100px;margin:0 auto;padding:32px 24px 60px}}
 h1{{font:800 30px/1.15 'Segoe UI',Arial,sans-serif;margin:0 0 6px}}
 h2{{font:800 20px/1.2 'Segoe UI',Arial,sans-serif;margin:34px 0 12px}}
 .badge{{display:inline-block;background:{status_color};color:#fff;border-radius:999px;padding:3px 12px;font:700 13px 'Segoe UI',Arial,sans-serif}}
 .sub{{color:#5c6470;margin:0 0 20px}}
 video{{width:100%;max-width:420px;border-radius:14px;background:#000;display:block;margin:10px 0 20px}}
 table{{border-collapse:collapse;width:100%;background:#fff;border-radius:10px;overflow:hidden;font:14px 'Segoe UI',Arial,sans-serif}}
 th,td{{text-align:left;padding:9px 12px;border-bottom:1px solid #e6e0d3}}
 th{{background:#18202e;color:#fff;font-weight:700}}
 .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}
 figure{{margin:0;background:#fff;border-radius:10px;overflow:hidden}}
 figure img{{width:100%;display:block}}
 figcaption{{padding:6px 8px;font:12px 'Segoe UI',Arial,sans-serif;color:#5c6470}}
 .cols{{display:grid;grid-template-columns:1fr 1fr;gap:24px}}
 @media(max-width:760px){{.grid{{grid-template-columns:repeat(2,1fr)}}.cols{{grid-template-columns:1fr}}}}
 code{{background:#e9e3d5;padding:1px 5px;border-radius:4px}}
</style></head><body><main>
<h1>{html.escape(str(manifest.get('slug')))}</h1>
<p class="sub">{html.escape(str(brief.get('name', '')))} &nbsp;·&nbsp;
{manifest.get('canvas', {}).get('w')}×{manifest.get('canvas', {}).get('h')} @ {manifest.get('canvas', {}).get('fps')} fps
&nbsp;·&nbsp; {manifest.get('footage_seconds', 0)}s</p>
<p><span class="badge">VERIFY {status}</span>
&nbsp; {html.escape(', '.join(problems)) if problems else 'dimensions, fps, duration and audio passed'}</p>

<h2>Master</h2>
<video controls src="{html.escape(rel_video)}"></video>
<p><a href="{html.escape(rel_video)}">Open the MP4</a> &nbsp;·&nbsp;
<a href="{html.escape(rel_sheet)}">Contact sheet</a></p>

<h2>Contact sheet</h2>
<img src="{html.escape(rel_sheet)}" style="width:100%;border-radius:10px">

<h2>Frames</h2>
<div class="grid">{frame_tags}</div>

<div class="cols">
<div><h2>AI spend</h2>
<p>{spend_label}: <strong>${session_spend:.2f}</strong> of a ${brief.get('budget', {}).get('limit_usd', 0):.2f} cap.
{f'<br>Conservative with 10% buffer: ${fal.get("session_conservative_usd", 0):.2f}' if fal.get("session_conservative_usd") else ''}
{f'<br><a href="COST-REPORT.md">Full cost report</a>' if os.path.exists(os.path.join(out_dir, "COST-REPORT.md")) else ''}</p>
<table><tr><th>Shot</th><th>Model</th><th>USD</th><th>Request</th></tr>{job_rows}</table></div>
<div><h2>Audio</h2>
<p>Integrated loudness: <strong>{audio.get('integrated_lufs', 'n/a')} LUFS</strong><br>
True peak: {audio.get('true_peak_dbfs', 'n/a')} dBFS</p>
<p>Mix: <code>{html.escape(str(audio.get('mix', '')))}</code></p></div>
</div>

<h2>B-roll licences</h2>
<table><tr><th>Clip</th><th>Licence</th><th>Source</th></tr>{lic_rows}</table>

<h2>Brief</h2>
<p><code>{html.escape(str(manifest.get('brief', '')))}</code></p>
<p>Approval status: <strong>local review master, not published.</strong>
{f' &nbsp;·&nbsp; <a href="REVIEW-NOTES.md">Human review checklist</a>' if os.path.exists(os.path.join(out_dir, "REVIEW-NOTES.md")) else ''}</p>
</main></body></html>"""
    path = os.path.join(out_dir, "review.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    return path
