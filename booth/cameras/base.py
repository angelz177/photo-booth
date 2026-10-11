"""Camera adapter interface.

Every adapter produces JPEG bytes. ``preview_frame`` is the low-resolution
live view used for the on-screen viewfinder; ``capture`` is the full-quality
still. Adapters are not thread-safe: the CameraManager serializes all calls.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class CameraError(RuntimeError):
    """Raised for any camera failure. The message is shown to the operator."""


class Camera(ABC):
    name: str = "base"

    @abstractmethod
    def open(self) -> None:
        """Connect to the camera. Raise CameraError if none is available."""

    @abstractmethod
    def close(self) -> None:
        """Release the camera. Must be safe to call more than once."""

    @abstractmethod
    def preview_frame(self) -> bytes:
        """Return one live-view frame as JPEG bytes."""

    @abstractmethod
    def capture(self) -> bytes:
        """Take a photo and return the full-size JPEG bytes."""

    def info(self) -> dict:
        return {"adapter": self.name}
