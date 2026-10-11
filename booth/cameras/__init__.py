"""Camera adapter registry."""

from __future__ import annotations

from .base import Camera, CameraError

ADAPTERS = ("mock", "webcam", "gphoto2", "edsdk")


def make_camera(name: str, **kwargs) -> Camera:
    if name == "mock":
        from .mock import MockCamera

        return MockCamera()
    if name == "webcam":
        from .webcam import WebcamCamera

        return WebcamCamera(index=int(kwargs.get("webcam_index", 0)))
    if name == "gphoto2":
        from .gphoto2_camera import GPhoto2Camera

        return GPhoto2Camera()
    if name == "edsdk":
        from .edsdk_camera import EdsdkCamera

        return EdsdkCamera(dll_dir=kwargs.get("edsdk_dir"))
    raise ValueError(f"Unknown camera adapter '{name}'. Choose one of: {', '.join(ADAPTERS)}")


__all__ = ["Camera", "CameraError", "ADAPTERS", "make_camera"]
