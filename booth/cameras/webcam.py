"""USB webcam adapter via OpenCV.

Also works for mirrorless/DSLR bodies switched into their UVC "webcam" mode,
with the caveat that the still is a video frame, not a full-resolution photo.
"""

from __future__ import annotations

from .base import Camera, CameraError


class WebcamCamera(Camera):
    name = "webcam"

    def __init__(self, index: int = 0, preview_width: int = 960):
        self.index = index
        self.preview_width = preview_width
        self._cap = None
        self._cv2 = None

    def open(self) -> None:
        try:
            import cv2  # type: ignore
        except ImportError as exc:
            raise CameraError(
                "OpenCV is not installed. Run: pip install 'photo-booth[webcam]'"
            ) from exc
        self._cv2 = cv2
        cap = cv2.VideoCapture(self.index)
        if not cap.isOpened():
            raise CameraError(f"No webcam found at index {self.index}")
        # Ask for the largest common size; the driver picks the closest it supports.
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        self._cap = cap

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _read(self):
        if self._cap is None:
            raise CameraError("Webcam is not open")
        ok, frame = self._cap.read()
        if not ok or frame is None:
            raise CameraError("Webcam returned no frame (unplugged?)")
        return frame

    def _encode(self, frame, quality: int) -> bytes:
        cv2 = self._cv2
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            raise CameraError("JPEG encode failed")
        return buf.tobytes()

    def preview_frame(self) -> bytes:
        cv2 = self._cv2
        frame = self._read()
        h, w = frame.shape[:2]
        if w > self.preview_width:
            scale = self.preview_width / w
            frame = cv2.resize(frame, (self.preview_width, int(h * scale)))
        return self._encode(frame, 70)

    def capture(self) -> bytes:
        # Drop a couple of buffered frames so the still matches "now".
        for _ in range(2):
            self._read()
        return self._encode(self._read(), 95)

    def info(self) -> dict:
        return {"adapter": self.name, "model": f"Webcam #{self.index}"}
