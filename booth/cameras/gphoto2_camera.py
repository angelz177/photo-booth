"""Canon (and Nikon/Sony/Fuji) tethered capture over USB via libgphoto2.

Tested path: Canon EOS DSLR / mirrorless in PTP mode on Linux or macOS.

Setup notes
-----------
* Linux: ``sudo apt install libgphoto2-dev`` then ``pip install gphoto2``
  (or use the prebuilt wheel, which bundles libgphoto2). If the desktop
  auto-mounts the camera, stop it:
  ``gvfs-gphoto2-volume-monitor`` must not be running.
* macOS: ``brew install libgphoto2`` then ``pip install gphoto2``.
  macOS grabs the camera for Image Capture; run ``killall PTPCamera`` (older)
  or ``killall ptpcamerad`` (newer) after plugging the camera in.
* On the camera: set the lens to manual focus after framing the booth, pick
  JPEG (not RAW) as the image format, disable auto power-off, and keep the
  camera in a normal shooting mode (M or Av), not in its own webcam/UVC mode.
"""

from __future__ import annotations

from .base import Camera, CameraError

_JPEG_MAGIC = b"\xff\xd8"


class GPhoto2Camera(Camera):
    name = "gphoto2"

    def __init__(self, capture_target_ram: bool = True):
        self.capture_target_ram = capture_target_ram
        self._gp = None
        self._camera = None
        self._model = "unknown"
        self._live_view_active = False

    # -- lifecycle -------------------------------------------------------

    def open(self) -> None:
        try:
            import gphoto2 as gp  # type: ignore
        except ImportError as exc:
            raise CameraError(
                "python-gphoto2 is not installed. Run: pip install 'photo-booth[gphoto2]'"
            ) from exc
        self._gp = gp
        camera = gp.Camera()
        try:
            camera.init()
        except gp.GPhoto2Error as exc:
            raise CameraError(self._explain(exc)) from exc
        self._camera = camera
        try:
            self._model = camera.get_abilities().model
        except gp.GPhoto2Error:
            pass
        if self.capture_target_ram:
            # Keep photos in camera RAM so they download fast and don't fill the card.
            self._set_config("capturetarget", "Internal RAM")

    def close(self) -> None:
        if self._camera is None:
            return
        try:
            if self._live_view_active:
                self._set_config("viewfinder", 0)
        except Exception:
            pass
        try:
            self._camera.exit()
        except Exception:
            pass
        self._camera = None
        self._live_view_active = False

    # -- frames ----------------------------------------------------------

    def preview_frame(self) -> bytes:
        gp, camera = self._require()
        try:
            camera_file = camera.capture_preview()
            self._live_view_active = True
            return bytes(camera_file.get_data_and_size())
        except gp.GPhoto2Error as exc:
            raise CameraError(self._explain(exc)) from exc

    def capture(self) -> bytes:
        gp, camera = self._require()
        try:
            path = camera.capture(gp.GP_CAPTURE_IMAGE)
            camera_file = camera.file_get(path.folder, path.name, gp.GP_FILE_TYPE_NORMAL)
            data = bytes(camera_file.get_data_and_size())
            try:
                camera.file_delete(path.folder, path.name)
            except gp.GPhoto2Error:
                pass  # files captured to RAM vanish on their own
        except gp.GPhoto2Error as exc:
            raise CameraError(self._explain(exc)) from exc
        if not data.startswith(_JPEG_MAGIC):
            raise CameraError(
                f"Camera returned {path.name}, which is not a JPEG. "
                "Set the camera's image quality to JPEG (not RAW)."
            )
        return data

    def info(self) -> dict:
        return {"adapter": self.name, "model": self._model}

    # -- helpers ---------------------------------------------------------

    def _require(self):
        if self._camera is None or self._gp is None:
            raise CameraError("Camera is not open")
        return self._gp, self._camera

    def _set_config(self, name: str, value) -> bool:
        """Best-effort config write; returns False if the camera lacks the setting."""
        gp, camera = self._require()
        try:
            config = camera.get_config()
            widget = config.get_child_by_name(name)
            widget.set_value(value)
            camera.set_config(config)
            return True
        except gp.GPhoto2Error:
            return False

    def _explain(self, exc) -> str:
        gp = self._gp
        code = getattr(exc, "code", None)
        if code == gp.GP_ERROR_MODEL_NOT_FOUND:
            return "No camera detected. Check the USB cable and that the camera is on."
        if code == gp.GP_ERROR_IO_USB_CLAIM:
            return (
                "Another program has the camera (USB claim failed). On Linux stop "
                "gvfs-gphoto2-volume-monitor; on macOS run 'killall ptpcamerad' "
                "or 'killall PTPCamera'."
            )
        return f"Camera error: {exc}"
