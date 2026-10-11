"""Synthetic camera for development and tests: no hardware needed."""

from __future__ import annotations

import io
import math
import time
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from .base import Camera

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


class MockCamera(Camera):
    name = "mock"

    def __init__(self, preview_size=(960, 640), capture_size=(1920, 1280)):
        self.preview_size = preview_size
        self.capture_size = capture_size
        self._t0 = time.monotonic()
        self._bg = self._gradient(capture_size)
        self._frames = 0

    def open(self) -> None:
        self._frames = 0

    def close(self) -> None:
        pass

    @staticmethod
    def _gradient(size: tuple[int, int]) -> Image.Image:
        w, h = size
        img = Image.new("RGB", (w, h))
        px = img.load()
        for y in range(h):
            for x in range(0, w, 4):
                r = int(40 + 60 * x / w)
                g = int(30 + 40 * y / h)
                b = int(80 + 100 * (1 - y / h))
                for dx in range(4):
                    if x + dx < w:
                        px[x + dx, y] = (r, g, b)
        return img

    def _render(self, size: tuple[int, int], label: str, quality: int) -> bytes:
        w, h = size
        t = time.monotonic() - self._t0
        img = self._bg.resize(size) if size != self._bg.size else self._bg.copy()
        draw = ImageDraw.Draw(img)
        # A bouncing "face" so motion is visible in the viewfinder.
        cx = int(w / 2 + (w * 0.3) * math.sin(t * 1.2))
        cy = int(h / 2 + (h * 0.2) * math.cos(t * 0.8))
        r = int(min(w, h) * 0.18)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(250, 220, 170), outline=(255, 255, 255), width=4)
        eye = r // 5
        draw.ellipse((cx - r // 2 - eye, cy - r // 3 - eye, cx - r // 2 + eye, cy - r // 3 + eye), fill=(30, 30, 30))
        draw.ellipse((cx + r // 2 - eye, cy - r // 3 - eye, cx + r // 2 + eye, cy - r // 3 + eye), fill=(30, 30, 30))
        draw.arc((cx - r // 2, cy, cx + r // 2, cy + r // 2), 10, 170, fill=(30, 30, 30), width=max(2, r // 12))
        big = _font(int(h * 0.07))
        small = _font(int(h * 0.04))
        draw.text((24, 16), label, font=big, fill=(255, 255, 255))
        draw.text((24, h - int(h * 0.04) - 20), datetime.now().strftime("%H:%M:%S"), font=small, fill=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        return buf.getvalue()

    def preview_frame(self) -> bytes:
        self._frames += 1
        return self._render(self.preview_size, "MOCK CAMERA  live view", 70)

    def capture(self) -> bytes:
        time.sleep(0.3)  # imitate shutter + download latency
        return self._render(self.capture_size, "MOCK CAMERA  photo", 92)

    def info(self) -> dict:
        return {"adapter": self.name, "model": "Mock camera (synthetic frames)"}
