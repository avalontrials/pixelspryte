"""Purpose-built graphic cards for the reel (beyond the generic text card)."""
from __future__ import annotations

import os

from PIL import Image, ImageDraw

from .overlay import CREAM, GOLD, NAVY, _font, _wrap

RED = (196, 76, 58, 255)
GREEN = (54, 120, 96, 255)
INK = (24, 31, 43, 255)


def _canvas(size):
    return Image.new("RGBA", size, (0, 0, 0, 0))


def equity_split(out_path: str, canvas: tuple[int, int], *,
                 left_label: str = "AVG EQUITY GAIN",
                 left_value: str = "+$85,000",
                 left_note: str = "3+ years of ownership",
                 right_label: str = "6% COMMISSION",
                 right_value: str = "−$28,500",
                 right_note: str = "at the closing table",
                 kicker: str = "THE MATH",
                 footer: str = "Same sale. You keep the difference.") -> str:
    w, h = canvas
    img = _canvas(canvas)
    d = ImageDraw.Draw(img)

    margin = int(w * 0.055)
    gap = int(w * 0.025)
    panel_w = (w - 2 * margin - gap) // 2
    top = int(h * 0.225)
    panel_h = int(h * 0.33)
    radius = int(w * 0.022)

    # Headline band
    f_kick = _font(int(h * 0.019), "Bold")
    d.text((w // 2, top - int(h * 0.045)), kicker.upper(), font=f_kick, fill=GOLD, anchor="ma")

    for x, label, value, note, color in (
        (margin, left_label, left_value, left_note, GREEN),
        (margin + panel_w + gap, right_label, right_value, right_note, RED),
    ):
        d.rounded_rectangle([x, top, x + panel_w, top + panel_h], radius=radius,
                            fill=(10, 16, 27, 232), outline=color, width=max(2, int(w * 0.003)))
        d.rounded_rectangle([x, top, x + panel_w, top + int(h * 0.012)], radius=radius // 2,
                            fill=color)
        f_label = _font(int(h * 0.0165), "Bold")
        for line in _wrap(d, label, f_label, panel_w - int(w * 0.05)):
            d.text((x + panel_w // 2, top + int(h * 0.055)), line, font=f_label,
                   fill=(214, 208, 196, 245), anchor="ma")
            break
        f_val = _font(int(h * 0.044), "Black")
        d.text((x + panel_w // 2, top + int(h * 0.115)), value, font=f_val,
               fill=CREAM, anchor="ma")
        f_note = _font(int(h * 0.0155), "Medium")
        lines = _wrap(d, note, f_note, panel_w - int(w * 0.05))
        yy = top + int(h * 0.185)
        for line in lines[:2]:
            d.text((x + panel_w // 2, yy), line, font=f_note,
                   fill=(198, 193, 182, 235), anchor="ma")
            yy += int(h * 0.025)

    f_foot = _font(int(h * 0.022), "Bold")
    d.rounded_rectangle([margin, top + panel_h + int(h * 0.025),
                         w - margin, top + panel_h + int(h * 0.095)],
                        radius=int(w * 0.012), fill=(0, 0, 0, 150))
    d.text((w // 2, top + panel_h + int(h * 0.060)), footer, font=f_foot,
           fill=CREAM, anchor="mm")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.save(out_path)
    return out_path


def quote_card(out_path: str, canvas: tuple[int, int], *,
               quote: str, attribution: str, kicker: str = "HOMEOWNER",
               y_frac: float = 0.25) -> str:
    w, h = canvas
    img = _canvas(canvas)
    d = ImageDraw.Draw(img)
    margin = int(w * 0.065)
    y = int(h * y_frac)

    f_kick = _font(int(h * 0.019), "Bold")
    d.text((margin, y), kicker.upper(), font=f_kick, fill=GOLD, anchor="la")
    y += int(h * 0.045)

    f_quote = _font(int(h * 0.040), "Black")
    lines = _wrap(d, quote, f_quote, w - 2 * margin)
    pad = int(h * 0.022)
    block_h = int(h * 0.052) * len(lines)
    d.rounded_rectangle([margin - pad, y - pad, w - margin + pad, y + block_h + pad],
                        radius=int(w * 0.018), fill=(0, 0, 0, 168))
    for line in lines:
        d.text((w // 2, y), line, font=f_quote, fill=CREAM, anchor="ma")
        y += int(h * 0.052)

    f_attr = _font(int(h * 0.019), "SemiBold")
    d.text((w // 2, y + int(h * 0.020)), attribution, font=f_attr,
           fill=(212, 206, 194, 240), anchor="ma")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.save(out_path)
    return out_path


def end_card(out_path: str, canvas: tuple[int, int], *,
             brand: str = "YOUR BRAND",
             headline: str = "UNLOCK YOUR VERIFIED COMP VALUE",
             subhead: str = "Keep 100% of your equity.",
             cta: str = "LINK IN PROFILE") -> str:
    w, h = canvas
    img = Image.new("RGBA", canvas, (11, 19, 35, 255))
    d = ImageDraw.Draw(img)

    # Quiet gold rule + brand
    d.rectangle([int(w * 0.10), int(h * 0.14), int(w * 0.90), int(h * 0.142)], fill=GOLD)
    f_brand = _font(int(h * 0.030), "Black")
    d.text((w // 2, int(h * 0.17)), brand, font=f_brand, fill=CREAM, anchor="ma")

    f_head = _font(int(h * 0.046), "Black")
    y = int(h * 0.34)
    for line in _wrap(d, headline, f_head, int(w * 0.82)):
        d.text((w // 2, y), line, font=f_head, fill=CREAM, anchor="ma")
        y += int(h * 0.056)

    f_sub = _font(int(h * 0.024), "Medium")
    d.text((w // 2, y + int(h * 0.025)), subhead, font=f_sub,
           fill=(206, 200, 188, 240), anchor="ma")

    d.rounded_rectangle([int(w * 0.14), int(h * 0.72), int(w * 0.86), int(h * 0.795)],
                        radius=int(w * 0.02), fill=GOLD)
    f_cta = _font(int(h * 0.024), "Black")
    d.text((w // 2, int(h * 0.7575)), cta, font=f_cta, fill=NAVY, anchor="mm")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.save(out_path)
    return out_path
