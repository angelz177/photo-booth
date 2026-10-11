"""Canon EOS over USB on Windows via Canon's official EDSDK.

Canon does not allow redistributing the SDK, so it is not in this repo. Get it
from the Canon Developer Community (free registration, approval takes a few
business days), then copy the contents of its ``EDSDK_64/Dll`` folder
(EDSDK.dll, EdsImage.dll and friends) into ``./EDSDK`` next to this project,
or point ``--edsdk-dir`` / ``BOOTH_EDSDK_DIR`` at that folder.

Threading: EDSDK is single-threaded and, on Windows, needs its own COM
apartment plus a periodic ``EdsGetEvent`` pump. This adapter therefore runs
one dedicated worker thread that owns the SDK; every public method posts a job
to that thread and waits for the result. The CameraManager may call it from
any thread.

The ctypes layer (``CtypesEdsApi``) is a direct transcription of EDSDK.h. The
control flow around it (``EdsdkCamera``) is unit-tested with a fake API.
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Protocol

from .base import Camera, CameraError

log = logging.getLogger(__name__)

# --- EDSDK.h constants ----------------------------------------------------

EDS_ERR_OK = 0x0000
EDS_ERR_DEVICE_NOT_FOUND = 0x0080
EDS_ERR_DEVICE_BUSY = 0x0081
EDS_ERR_OBJECT_NOTREADY = 0xA102
EDS_ERR_TAKE_PICTURE_AF_NG = 0x8D01
EDS_ERR_COMM_DISCONNECTED = 0x00C1

ERROR_MESSAGES = {
    EDS_ERR_DEVICE_NOT_FOUND: "No Canon camera found. Check the USB cable and that the camera is on.",
    EDS_ERR_DEVICE_BUSY: "The camera is busy. Wait a moment and try again.",
    EDS_ERR_TAKE_PICTURE_AF_NG: "Autofocus failed. Switch the lens to MF after framing, or add light.",
    EDS_ERR_COMM_DISCONNECTED: "The camera disconnected. Check the USB cable.",
    0x8D02: "Autofocus not available in this mode.",
    0x8D03: "Mirror is up; cannot take a picture.",
    0x8D04: "Sensor cleaning is running.",
    0x8D05: "Silent mode blocks capture.",
    0x8D06: "No memory card in the camera.",
    0x8D07: "The memory card is write-protected.",
    0x8D08: "The memory card is full.",
    0x8D09: "The camera's lens is not attached.",
    0x8D0A: "Flash is charging.",
    0x8D0B: "Camera is in the wrong mode for remote capture.",
    0x8D0C: "The card is not formatted.",
    0x8D0D: "Low battery.",
    0x8D0E: "Movie mode is active; switch the camera to still photo mode.",
}

kEdsPropID_ProductName = 0x0002
kEdsPropID_SaveTo = 0x000B
kEdsPropID_Capacity = 0x000C
kEdsPropID_Evf_OutputDevice = 0x0500
kEdsSaveTo_Host = 2
kEdsEvfOutputDevice_PC = 2

kEdsCameraCommand_TakePicture = 0x0000
kEdsCameraCommand_ExtendShutDownTimer = 0x0001
kEdsCameraCommand_PressShutterButton = 0x0004
kEdsCameraCommand_ShutterButton_OFF = 0x0000
kEdsCameraCommand_ShutterButton_Completely = 0x0003

kEdsObjectEvent_All = 0x0200
kEdsObjectEvent_DirItemCreated = 0x0204
kEdsObjectEvent_DirItemRequestTransfer = 0x0208
kEdsStateEvent_All = 0x0300
kEdsStateEvent_Shutdown = 0x0301


class EdsError(RuntimeError):
    def __init__(self, code: int, where: str = ""):
        self.code = code
        super().__init__(f"{where} failed: 0x{code:04X}".strip())


class EdsApi(Protocol):
    """The slice of EDSDK this adapter needs, as plain Python calls.

    Every method raises EdsError on a non-OK return code, except
    ``download_evf`` which returns None while live view is not ready yet.
    """

    def initialize(self) -> None: ...
    def terminate(self) -> None: ...
    def first_camera(self): ...
    def open_session(self, camera) -> None: ...
    def close_session(self, camera) -> None: ...
    def device_name(self, camera) -> str: ...
    def set_uint32(self, camera, prop_id: int, value: int) -> None: ...
    def set_capacity(self, camera) -> None: ...
    def send_command(self, camera, command: int, param: int) -> None: ...
    def set_object_handler(self, camera, handler: Callable[[int, object], None]) -> None: ...
    def set_state_handler(self, camera, handler: Callable[[int, int], None]) -> None: ...
    def download_evf(self, camera) -> bytes | None: ...
    def download_item(self, item) -> bytes: ...
    def release(self, ref) -> None: ...
    def get_event(self) -> None: ...


# --- adapter ----------------------------------------------------------------


class EdsdkCamera(Camera):
    name = "edsdk"

    def __init__(self, dll_dir: str | os.PathLike | None = None, api: EdsApi | None = None,
                 capture_timeout: float = 15.0, evf_start_timeout: float = 5.0):
        self.dll_dir = Path(dll_dir) if dll_dir else None
        self._api = api
        self.capture_timeout = capture_timeout
        self.evf_start_timeout = evf_start_timeout
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._camera = None
        self._model = "unknown"
        self._evf_on = False
        self._photo: bytes | None = None  # filled by the object event handler
        self._photo_error: str | None = None
        self._shutdown = False
        self._open_error: Exception | None = None

    # -- Camera interface (any thread) ----------------------------------

    def open(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if self._api is None:
            self._api = CtypesEdsApi(self.dll_dir)
        self._stop.clear()
        self._shutdown = False
        self._open_error = None
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), name="edsdk", daemon=True)
        self._thread.start()
        ready.wait(timeout=30)
        if self._open_error is not None:
            self._thread.join(timeout=5)
            self._thread = None
            raise self._open_error

    def close(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=10)
        self._thread = None

    def preview_frame(self) -> bytes:
        return self._call(self._job_preview)

    def capture(self) -> bytes:
        return self._call(self._job_capture, timeout=self.capture_timeout + 5)

    def info(self) -> dict:
        return {"adapter": self.name, "model": self._model}

    # -- job marshalling --------------------------------------------------

    def _call(self, fn: Callable[[], bytes], timeout: float = 10.0) -> bytes:
        if self._shutdown:
            raise CameraError(ERROR_MESSAGES[EDS_ERR_COMM_DISCONNECTED])
        if self._thread is None or not self._thread.is_alive():
            raise CameraError("Camera is not open")
        done = threading.Event()
        box: dict = {}

        def job():
            try:
                box["result"] = fn()
            except BaseException as exc:  # noqa: BLE001 - propagate to caller thread
                box["error"] = exc
            finally:
                done.set()

        self._jobs.put(job)
        if not done.wait(timeout):
            raise CameraError("The camera did not respond in time")
        if "error" in box:
            exc = box["error"]
            if isinstance(exc, CameraError):
                raise exc
            if isinstance(exc, EdsError):
                raise CameraError(self._explain(exc)) from exc
            raise CameraError(f"{type(exc).__name__}: {exc}") from exc
        return box["result"]

    # -- worker thread ----------------------------------------------------

    def _run(self, ready: threading.Event) -> None:
        api = self._api
        try:
            api.initialize()
            camera = api.first_camera()
            if camera is None:
                raise EdsError(EDS_ERR_DEVICE_NOT_FOUND, "EdsGetCameraList")
            api.set_object_handler(camera, self._on_object_event)
            api.set_state_handler(camera, self._on_state_event)
            api.open_session(camera)
            self._camera = camera
            try:
                self._model = api.device_name(camera)
            except EdsError:
                pass
            # Deliver photos to the PC instead of the memory card.
            api.set_uint32(camera, kEdsPropID_SaveTo, kEdsSaveTo_Host)
            api.set_capacity(camera)
        except Exception as exc:  # noqa: BLE001
            self._open_error = CameraError(self._explain(exc)) if isinstance(exc, EdsError) else CameraError(str(exc))
            try:
                api.terminate()
            except Exception:
                pass
            ready.set()
            return
        ready.set()
        log.info("EDSDK session open: %s", self._model)

        last_keepalive = time.monotonic()
        try:
            while not self._stop.is_set():
                try:
                    job = self._jobs.get(timeout=0.01)
                except queue.Empty:
                    self._pump()
                    now = time.monotonic()
                    if now - last_keepalive > 60:
                        last_keepalive = now
                        try:
                            api.send_command(camera, kEdsCameraCommand_ExtendShutDownTimer, 0)
                        except EdsError:
                            pass
                    continue
                job()
        finally:
            self._teardown()

    def _pump(self) -> None:
        try:
            self._api.get_event()
        except EdsError as exc:
            log.debug("EdsGetEvent: %s", exc)

    def _teardown(self) -> None:
        api, camera = self._api, self._camera
        if camera is not None:
            for step in (
                lambda: api.set_uint32(camera, kEdsPropID_Evf_OutputDevice, 0) if self._evf_on else None,
                lambda: api.close_session(camera),
                lambda: api.release(camera),
            ):
                try:
                    step()
                except Exception:
                    pass
        try:
            api.terminate()
        except Exception:
            pass
        self._camera = None
        self._evf_on = False

    # -- jobs (worker thread) ----------------------------------------------

    def _start_evf(self) -> bytes:
        """(Re)enable live view output to the PC and wait for the first frame."""
        api, camera = self._api, self._camera
        api.set_uint32(camera, kEdsPropID_Evf_OutputDevice, kEdsEvfOutputDevice_PC)
        self._evf_on = True
        deadline = time.monotonic() + self.evf_start_timeout
        while time.monotonic() < deadline:
            frame = api.download_evf(camera)
            if frame:
                return frame
            self._pump()
            time.sleep(0.05)
        self._evf_on = False
        raise CameraError(
            "Live view did not start. On the camera enable 'Live View shooting' and use a still-photo mode."
        )

    def _job_preview(self) -> bytes:
        if not self._evf_on:
            return self._start_evf()
        frame = self._api.download_evf(self._camera)
        if frame:
            return frame
        # DSLRs drop live view after their own idle timeout (and right after a
        # shot). Re-enable it in place instead of reporting a disconnect.
        log.info("EDSDK: live view frame empty, re-enabling live view")
        return self._start_evf()

    def _job_capture(self) -> bytes:
        api, camera = self._api, self._camera
        self._photo = None
        self._photo_error = None
        try:
            api.send_command(camera, kEdsCameraCommand_TakePicture, 0)
        except EdsError as exc:
            if exc.code == EDS_ERR_TAKE_PICTURE_AF_NG:
                raise
            # Newer bodies prefer the shutter-button command.
            api.send_command(camera, kEdsCameraCommand_PressShutterButton, kEdsCameraCommand_ShutterButton_Completely)
            api.send_command(camera, kEdsCameraCommand_PressShutterButton, kEdsCameraCommand_ShutterButton_OFF)
        deadline = time.monotonic() + self.capture_timeout
        while self._photo is None and self._photo_error is None and time.monotonic() < deadline:
            self._pump()
            time.sleep(0.01)
        if self._photo_error:
            raise CameraError(self._photo_error)
        if self._photo is None:
            raise CameraError("The camera did not deliver the photo in time")
        return self._photo

    # -- SDK callbacks (worker thread, inside get_event) -------------------

    def _on_object_event(self, event: int, item) -> None:
        if event in (kEdsObjectEvent_DirItemRequestTransfer, kEdsObjectEvent_DirItemCreated):
            try:
                self._photo = self._api.download_item(item)
            except EdsError as exc:
                self._photo_error = self._explain(exc)
            finally:
                try:
                    self._api.release(item)
                except Exception:
                    pass
        else:
            try:
                self._api.release(item)
            except Exception:
                pass

    def _on_state_event(self, event: int, param: int) -> None:
        if event == kEdsStateEvent_Shutdown:
            log.warning("EDSDK: camera shut down / disconnected")
            self._shutdown = True
            self._stop.set()

    @staticmethod
    def _explain(exc: Exception) -> str:
        if isinstance(exc, EdsError):
            return ERROR_MESSAGES.get(exc.code, f"Canon SDK error 0x{exc.code:04X} ({exc})")
        return str(exc)


# --- ctypes binding (Windows only) --------------------------------------------


class CtypesEdsApi:
    """Thin ctypes wrapper over EDSDK.dll. Only built on Windows."""

    def __init__(self, dll_dir: Path | None):
        if not sys.platform.startswith("win"):
            raise CameraError("The EDSDK adapter only works on Windows. Use --camera gphoto2 on Linux/macOS.")
        import ctypes
        from ctypes import wintypes

        self.ct = ctypes
        dll_dir = Path(dll_dir or os.environ.get("BOOTH_EDSDK_DIR", "EDSDK")).resolve()
        dll_path = dll_dir / "EDSDK.dll"
        if not dll_path.exists():
            raise CameraError(
                f"EDSDK.dll not found in {dll_dir}. Copy Canon's EDSDK_64/Dll folder contents there "
                "or set --edsdk-dir."
            )
        os.add_dll_directory(str(dll_dir))
        self.dll = ctypes.WinDLL(str(dll_path))

        c = ctypes
        u32, i32, u64 = c.c_uint32, c.c_int32, c.c_uint64
        ref = c.c_void_p

        class DeviceInfo(c.Structure):
            _fields_ = [("szPortName", c.c_char * 256), ("szDeviceDescription", c.c_char * 256),
                        ("deviceSubType", u32), ("reserved", u32)]

        class DirectoryItemInfo(c.Structure):
            _fields_ = [("size", u64), ("isFolder", i32), ("groupID", u32), ("option", u32),
                        ("szFileName", c.c_char * 256), ("format", u32), ("dateTime", u32)]

        class Capacity(c.Structure):
            _fields_ = [("numberOfFreeClusters", i32), ("bytesPerSector", i32), ("reset", i32)]

        self.DeviceInfo, self.DirectoryItemInfo, self.Capacity = DeviceInfo, DirectoryItemInfo, Capacity
        self.ObjectHandler = c.WINFUNCTYPE(u32, u32, ref, ref)
        self.StateHandler = c.WINFUNCTYPE(u32, u32, u32, ref)
        self._handlers: list = []  # keep callbacks alive

        sig = {
            "EdsInitializeSDK": [], "EdsTerminateSDK": [], "EdsGetEvent": [],
            "EdsGetCameraList": [c.POINTER(ref)],
            "EdsGetChildCount": [ref, c.POINTER(u32)],
            "EdsGetChildAtIndex": [ref, i32, c.POINTER(ref)],
            "EdsRelease": [ref],
            "EdsOpenSession": [ref], "EdsCloseSession": [ref],
            "EdsGetDeviceInfo": [ref, c.POINTER(DeviceInfo)],
            "EdsSetPropertyData": [ref, u32, i32, u32, c.c_void_p],
            "EdsSendCommand": [ref, u32, i32],
            "EdsSetObjectEventHandler": [ref, u32, self.ObjectHandler, ref],
            "EdsSetCameraStateEventHandler": [ref, u32, self.StateHandler, ref],
            "EdsCreateMemoryStream": [u64, c.POINTER(ref)],
            "EdsCreateEvfImageRef": [ref, c.POINTER(ref)],
            "EdsDownloadEvfImage": [ref, ref],
            "EdsGetPointer": [ref, c.POINTER(c.c_void_p)],
            "EdsGetLength": [ref, c.POINTER(u64)],
            "EdsGetDirectoryItemInfo": [ref, c.POINTER(DirectoryItemInfo)],
            "EdsDownload": [ref, u64, ref],
            "EdsDownloadComplete": [ref],
        }
        for name, argtypes in sig.items():
            fn = getattr(self.dll, name)
            fn.argtypes = argtypes
            fn.restype = u32

        self._ole32 = ctypes.WinDLL("ole32")

    def _check(self, code: int, where: str) -> None:
        if code != EDS_ERR_OK:
            raise EdsError(code, where)

    # lifecycle
    def initialize(self) -> None:
        self._ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
        self._check(self.dll.EdsInitializeSDK(), "EdsInitializeSDK")

    def terminate(self) -> None:
        self.dll.EdsTerminateSDK()
        self._ole32.CoUninitialize()

    def first_camera(self):
        c = self.ct
        cam_list = c.c_void_p()
        self._check(self.dll.EdsGetCameraList(c.byref(cam_list)), "EdsGetCameraList")
        try:
            count = c.c_uint32()
            self._check(self.dll.EdsGetChildCount(cam_list, c.byref(count)), "EdsGetChildCount")
            if count.value == 0:
                return None
            camera = c.c_void_p()
            self._check(self.dll.EdsGetChildAtIndex(cam_list, 0, c.byref(camera)), "EdsGetChildAtIndex")
            return camera
        finally:
            self.dll.EdsRelease(cam_list)

    def open_session(self, camera) -> None:
        self._check(self.dll.EdsOpenSession(camera), "EdsOpenSession")

    def close_session(self, camera) -> None:
        self._check(self.dll.EdsCloseSession(camera), "EdsCloseSession")

    def device_name(self, camera) -> str:
        info = self.DeviceInfo()
        self._check(self.dll.EdsGetDeviceInfo(camera, self.ct.byref(info)), "EdsGetDeviceInfo")
        return info.szDeviceDescription.decode("utf-8", "replace")

    def set_uint32(self, camera, prop_id: int, value: int) -> None:
        data = self.ct.c_uint32(value)
        self._check(self.dll.EdsSetPropertyData(camera, prop_id, 0, 4, self.ct.byref(data)),
                    f"EdsSetPropertyData(0x{prop_id:04X})")

    def set_capacity(self, camera) -> None:
        cap = self.Capacity(0x7FFFFFFF, 0x1000, 1)
        self._check(self.dll.EdsSetPropertyData(camera, kEdsPropID_Capacity, 0, self.ct.sizeof(cap), self.ct.byref(cap)),
                    "EdsSetPropertyData(Capacity)")

    def send_command(self, camera, command: int, param: int) -> None:
        self._check(self.dll.EdsSendCommand(camera, command, param), f"EdsSendCommand(0x{command:04X})")

    def set_object_handler(self, camera, handler) -> None:
        def cb(event, item, _ctx):
            try:
                handler(event, item)
            except Exception:
                log.exception("object handler")
            return EDS_ERR_OK
        c_cb = self.ObjectHandler(cb)
        self._handlers.append(c_cb)
        self._check(self.dll.EdsSetObjectEventHandler(camera, kEdsObjectEvent_All, c_cb, None), "EdsSetObjectEventHandler")

    def set_state_handler(self, camera, handler) -> None:
        def cb(event, param, _ctx):
            try:
                handler(event, param)
            except Exception:
                log.exception("state handler")
            return EDS_ERR_OK
        c_cb = self.StateHandler(cb)
        self._handlers.append(c_cb)
        self._check(self.dll.EdsSetCameraStateEventHandler(camera, kEdsStateEvent_All, c_cb, None), "EdsSetCameraStateEventHandler")

    def _stream_bytes(self, stream) -> bytes:
        c = self.ct
        ptr, length = c.c_void_p(), c.c_uint64()
        self._check(self.dll.EdsGetPointer(stream, c.byref(ptr)), "EdsGetPointer")
        self._check(self.dll.EdsGetLength(stream, c.byref(length)), "EdsGetLength")
        return c.string_at(ptr, length.value)

    def download_evf(self, camera) -> bytes | None:
        c = self.ct
        stream, evf = c.c_void_p(), c.c_void_p()
        self._check(self.dll.EdsCreateMemoryStream(0, c.byref(stream)), "EdsCreateMemoryStream")
        try:
            self._check(self.dll.EdsCreateEvfImageRef(stream, c.byref(evf)), "EdsCreateEvfImageRef")
            try:
                code = self.dll.EdsDownloadEvfImage(camera, evf)
                if code in (EDS_ERR_OBJECT_NOTREADY, EDS_ERR_DEVICE_BUSY):
                    return None
                self._check(code, "EdsDownloadEvfImage")
                return self._stream_bytes(stream)
            finally:
                self.dll.EdsRelease(evf)
        finally:
            self.dll.EdsRelease(stream)

    def download_item(self, item) -> bytes:
        c = self.ct
        info = self.DirectoryItemInfo()
        self._check(self.dll.EdsGetDirectoryItemInfo(item, c.byref(info)), "EdsGetDirectoryItemInfo")
        stream = c.c_void_p()
        self._check(self.dll.EdsCreateMemoryStream(0, c.byref(stream)), "EdsCreateMemoryStream")
        try:
            self._check(self.dll.EdsDownload(item, info.size, stream), "EdsDownload")
            self._check(self.dll.EdsDownloadComplete(item), "EdsDownloadComplete")
            return self._stream_bytes(stream)
        finally:
            self.dll.EdsRelease(stream)

    def release(self, ref) -> None:
        if ref:
            self.dll.EdsRelease(ref)

    def get_event(self) -> None:
        self.dll.EdsGetEvent()
