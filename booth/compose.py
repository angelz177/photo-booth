"""Compose captured photos into a printable layout with Pillow.

All print output is 4x6 inches at 300 dpi, portrait (1200x1800). The "strip"
layout renders a 2x6 strip (600x1800) and doubles it onto the 4x6 sheet so a
single print cuts into two strips.

Custom branding: drop a transparent PNG at ``<data_dir>/overlays/<layout>.png``
(600x1800 for strip, 1200x1800 for single and grid). It is composited on top
of the photos and replaces the default text footer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

PRINT_SIZE = (1200, 1800)
STRIP_SIZE = (600, 1800)
BACKGROUND = (255, 255, 255)
TEXT = (30, 30, 30)
SUBTEXT = (110, 110, 110)

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


@dataclass(frozen=True)
class Slot:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class LayoutSpec:
    canvas: tuple[int, int]
    slots: tuple[Slot, ...]
    footer_top: int  # y where the text footer starts
    doubled_on_print: bool = False


# Photos are 3:2 (DSLR native). Margins are 20 px.
LAYOUTS: dict[str, LayoutSpec] = {
    "strip": LayoutSpec(
        canvas=STRIP_SIZE,
        slots=(Slot(20, 20, 560, 373), Slot(20, 413, 560, 373), Slot(20, 806, 560, 373)),
        footer_top=1199,
        doubled_on_print=True,
    ),
    "single": LayoutSpec(
        canvas=PRINT_SIZE,
        slots=(Slot(20, 200, 1160, 773),),
        footer_top=1000,
    ),
    "grid": LayoutSpec(
        canvas=PRINT_SIZE,
        slots=(Slot(20, 20, 570, 380), Slot(610, 20, 570, 380), Slot(20, 420, 570, 380), Slot(610, 420, 570, 380)),
        footer_top=820,
    ),
}


def _font(size: int):
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int, min_size: int = 18):
    size = start_size
    while size > min_size:
        font = _font(size)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 4
    return _font(min_size)


def _load_photo(path: Path, size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        return ImageOps.fit(img, size, method=Image.Resampling.LANCZOS)


def _draw_footer(img: Image.Image, spec: LayoutSpec, event_name: str, footer_text: str, when: datetime) -> None:
    draw = ImageDraw.Draw(img)
    w, h = spec.canvas
    band_h = h - spec.footer_top
    cx = w // 2
    title_font = _fit_text(draw, event_name, w - 60, int(min(band_h * 0.22, w * 0.11)))
    sub = footer_text.strip() or when.strftime("%B %d, %Y")
    sub_font = _fit_text(draw, sub, w - 60, int(min(band_h * 0.11, w * 0.055)))
    title_h = title_font.getbbox(event_name)[3]
    sub_h = sub_font.getbbox(sub)[3]
    gap = int(band_h * 0.08)
    block = title_h + gap + sub_h
    y = spec.footer_top + (band_h - block) // 2
    draw.text((cx, y), event_name, font=title_font, fill=TEXT, anchor="ma")
    draw.text((cx, y + title_h + gap), sub, font=sub_font, fill=SUBTEXT, anchor="ma")


def compose(
    layout: str,
    photos: list[Path],
    event_name: str,
    footer_text: str = "",
    overlay_dir: Path | None = None,
    when: datetime | None = None,
) -> tuple[Image.Image, Image.Image]:
    """Return (print_image, share_image) for the given layout."""
    spec = LAYOUTS[layout]
    if len(photos) < len(spec.slots):
        raise ValueError(f"layout '{layout}' needs {len(spec.slots)} photos, got {len(photos)}")
    when = when or datetime.now()

    canvas = Image.new("RGB", spec.canvas, BACKGROUND)
    for slot, path in zip(spec.slots, photos):
        canvas.paste(_load_photo(path, (slot.w, slot.h)), (slot.x, slot.y))

    overlay_path = (overlay_dir / f"{layout}.png") if overlay_dir else None
    if overlay_path and overlay_path.exists():
        with Image.open(overlay_path) as overlay:
            overlay = overlay.convert("RGBA")
            if overlay.size != spec.canvas:
                overlay = overlay.resize(spec.canvas, Image.Resampling.LANCZOS)
            canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    else:
        _draw_footer(canvas, spec, event_name, footer_text, when)

    if spec.doubled_on_print:
        sheet = Image.new("RGB", PRINT_SIZE, BACKGROUND)
        sheet.paste(canvas, (0, 0))
        sheet.paste(canvas, (spec.canvas[0], 0))
        return sheet, canvas
    return canvas, canvas


def save_jpeg(img: Image.Image, path: Path, quality: int = 92) -> None:
    img.save(path, "JPEG", quality=quality, dpi=(300, 300), optimize=True)
