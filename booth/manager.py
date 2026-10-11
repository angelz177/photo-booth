"""CameraManager: owns the camera, runs live view, serializes capture.

* A background thread pulls preview frames while anyone is watching the
  live stream, and reconnects automatically if the camera drops.
* ``capture()`` takes the same lock, so a still never interleaves with a
  preview call (libgphoto2 is not thread-safe).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from .cameras.base import Camera, CameraError

log = logging.getLogger(__name__)


class CameraManager:
    def __init__(self, camera: Camera, preview_fps: float = 20.0, reconnect_seconds: float = 3.0):
        self.camera = camera
        self.preview_interval = 1.0 / preview_fps
        self.reconnect_seconds = reconnect_seconds
        self._lock = threading.RLock()
        self._frame: bytes | None = None
        self._seq = 0
        self._viewers = 0
        self._connected = False
        self._error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="camera-preview", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        with self._lock:
            self.camera.close()
            self._connected = False

    # -- status ----------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    def status(self) -> dict:
        info = self.camera.info() if self._connected else {"adapter": self.camera.name}
        return {
            **info,
            "connected": self._connected,
            "error": self._error,
            "viewers": self._viewers,
            "frame_seq": self._seq,
        }

    # -- capture ---------------------------------------------------------

    def capture(self) -> bytes:
        with self._lock:
            if not self._connected:
                raise CameraError(self._error or "Camera is not connected")
            try:
                return self.camera.capture()
            except CameraError as exc:
                self._mark_disconnected(str(exc))
                raise

    def latest_frame(self) -> bytes | None:
        return self._frame

    # -- live stream -----------------------------------------------------

    async def mjpeg(self, boundary: str = "frame", max_frames: int | None = None):
        """Async generator yielding multipart/x-mixed-replace chunks."""
        self._viewers += 1
        last_seq = -1
        sent = 0
        try:
            while max_frames is None or sent < max_frames:
                if self._seq != last_seq and self._frame is not None:
                    last_seq = self._seq
                    frame = self._frame
                    yield (
                        f"--{boundary}\r\nContent-Type: image/jpeg\r\n"
                        f"Content-Length: {len(frame)}\r\n\r\n"
                    ).encode() + frame + b"\r\n"
                    sent += 1
                await asyncio.sleep(0.01)
        finally:
            self._viewers -= 1

    # -- background thread -----------------------------------------------

    def _mark_disconnected(self, error: str) -> None:
        log.warning("camera disconnected: %s", error)
        self._error = error
        self._connected = False
        try:
            self.camera.close()
        except Exception:
            pass

    def _try_connect(self) -> None:
        with self._lock:
            try:
                self.camera.open()
                self._connected = True
                self._error = None
                log.info("camera connected: %s", self.camera.info())
            except CameraError as exc:
                self._error = str(exc)
            except Exception as exc:  # adapter bug or driver crash: keep the booth alive
                self._error = f"{type(exc).__name__}: {exc}"
                log.exception("camera open failed")

    def _run(self) -> None:
        next_retry = 0.0
        while not self._stop.is_set():
            if not self._connected:
                now = time.monotonic()
                if now >= next_retry:
                    self._try_connect()
                    next_retry = now + self.reconnect_seconds
                time.sleep(0.2)
                continue
            if self._viewers <= 0:
                time.sleep(0.1)
                continue
            started = time.monotonic()
            try:
                with self._lock:
                    frame = self.camera.preview_frame()
                self._frame = frame
                self._seq += 1
            except CameraError as exc:
                self._mark_disconnected(str(exc))
                continue
            except Exception as exc:
                self._mark_disconnected(f"{type(exc).__name__}: {exc}")
                log.exception("preview failed")
                continue
            elapsed = time.monotonic() - started
            if elapsed < self.preview_interval:
                time.sleep(self.preview_interval - elapsed)
