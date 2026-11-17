"""Transparent text/graphic cards for the overlay track.

Resolve cannot be scripted to place editable text on a track above footage
(Fusion titles always land on V1), so graphics are rendered here as RGBA PNGs
and carried into Resolve by the OTIO timeline. Crisp text, exact copy.
"""
from __future__ import annotations

import os
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "assets", "fonts", "Montserrat-ExtraBold.ttf")

NAVY = (11, 19, 35, 255)      # #0B1323
CREAM = (245, 240, 228, 255)
GOLD = (202, 152, 68, 255)    # #CA9844


def _font(size: int, weight: str = "Black") -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(FONT_PATH, size)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def card(
    out_path: str,
    canvas: tuple[int, int],
    headline: str | None = None,
    kicker: str | None = None,
    subhead: str | None = None,
    y_frac: float = 0.10,
    align: str = "center",
    margin_frac: float = 0.075,
    headline_frac: float = 0.062,
    kicker_frac: float = 0.020,
    scrim: bool = True,
) -> str:
    """Render one transparent overlay card. Returns the path."""
    w, h = canvas
    margin = int(w * margin_frac)
    max_w = w - 2 * margin

    # Draw the copy on its own layer first so the scrim can hug the real text box.
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    y = int(h * y_frac)

    if kicker:
        f = _font(int(h * kicker_frac))
        d.text((margin if align == "left" else w // 2, y), kicker.upper(),
               font=f, fill=GOLD, anchor="la" if align == "left" else "ma")
        y += int(h * kicker_frac * 1.9)

    if headline:
        f = _font(int(h * headline_frac))
        for line in _wrap(d, headline, f, max_w):
            d.text((margin if align == "left" else w // 2, y), line,
                   font=f, fill=CREAM, anchor="la" if align == "left" else "ma")
            y += int(h * headline_frac * 1.16)

    if subhead:
        f2 = _font(int(h * headline_frac * 0.52), "SemiBold")
        y += int(h * 0.008)
        for line in _wrap(d, subhead, f2, max_w):
            d.text((margin if align == "left" else w // 2, y), line,
                   font=f2, fill=(230, 226, 216, 235), anchor="la" if align == "left" else "ma")
            y += int(h * headline_frac * 0.66)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bbox = layer.getbbox()
    if scrim and bbox:
        pad_x, pad_y = int(w * 0.035), int(h * 0.018)
        band = ImageDraw.Draw(img)
        band.rectangle([max(0, bbox[0] - pad_x), max(0, bbox[1] - pad_y),
                        min(w, bbox[2] + pad_x), min(h, bbox[3] + pad_y)],
                       fill=(0, 0, 0, 118))
    img.alpha_composite(layer)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.save(out_path)
    return out_path


def caption_card(out_path: str, canvas: tuple[int, int], text: str,
                 y_frac: float = 0.72, size_frac: float = 0.034) -> str:
    """Burned caption card: bold uppercase copy in a dark box, reels-safe area."""
    w, h = canvas
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    margin = int(w * 0.07)
    size = int(h * size_frac)
    f = _font(size)
    lines = _wrap(d, text.upper(), f, w - 2 * margin)
    # reel captions read best in one or two lines: shrink until they fit
    while len(lines) > 2 and size > int(h * 0.020):
        size = int(size * 0.92)
        f = _font(size)
        lines = _wrap(d, text.upper(), f, w - 2 * margin)
    line_h = int(h * size_frac * 1.30)
    block_h = line_h * len(lines)
    y = int(h * y_frac) - block_h // 2
    pad = int(h * 0.012)
    box = [margin - pad, y - pad, w - margin + pad, y + block_h + pad]
    d.rounded_rectangle(box, radius=int(h * 0.008), fill=(0, 0, 0, 150))
    for i, line in enumerate(lines):
        d.text((w // 2, y + i * line_h), line, font=f, fill=CREAM, anchor="ma")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.save(out_path)
    return out_path


def lower_third(out_path: str, canvas: tuple[int, int], name: str, role: str) -> str:
    w, h = canvas
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x, y = int(w * 0.08), int(h * 0.76)
    bar_w = int(w * 0.012)
    d.rectangle([x, y, x + bar_w, y + int(h * 0.11)], fill=GOLD)
    f1, f2 = _font(int(h * 0.030)), _font(int(h * 0.019), "Medium")
    d.text((x + bar_w + int(w * 0.025), y), name, font=f1, fill=CREAM, anchor="la")
    d.text((x + bar_w + int(w * 0.025), y + int(h * 0.036)), role.upper(), font=f2, fill=GOLD, anchor="la")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.save(out_path)
    return out_path
