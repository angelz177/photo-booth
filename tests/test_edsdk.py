"""EDSDK adapter control flow, driven by a fake SDK (no Canon DLL needed)."""

import threading
import time

import pytest

from booth.cameras.base import CameraError
from booth.cameras.edsdk_camera import (
    EDS_ERR_DEVICE_NOT_FOUND,
    EDS_ERR_TAKE_PICTURE_AF_NG,
    EdsdkCamera,
    EdsError,
    kEdsCameraCommand_PressShutterButton,
    kEdsCameraCommand_TakePicture,
    kEdsObjectEvent_DirItemRequestTransfer,
    kEdsPropID_Evf_OutputDevice,
    kEdsPropID_SaveTo,
    kEdsSaveTo_Host,
    kEdsStateEvent_Shutdown,
)

JPEG = b"\xff\xd8photo"
EVF = b"\xff\xd8evf"


class FakeEdsApi:
    """Mimics EDSDK semantics: events are only delivered from get_event()."""

    def __init__(self, cameras=1, evf_warmup_calls=2, take_picture_error=None, af_fail=False):
        self.cameras = cameras
        self.evf_warmup_calls = evf_warmup_calls
        self.take_picture_error = take_picture_error
        self.af_fail = af_fail
        self.props = {}
        self.commands = []
        self.pending_events = []
        self.object_handler = None
        self.state_handler = None
        self.released = []
        self.session_open = False
        self.initialized = False
        self.thread_ids = set()
        self.evf_calls = 0

    def _track(self):
        self.thread_ids.add(threading.get_ident())

    def initialize(self):
        self._track(); self.initialized = True

    def terminate(self):
        self._track(); self.initialized = False

    def first_camera(self):
        self._track()
        return "CAM" if self.cameras else None

    def open_session(self, camera):
        self._track(); self.session_open = True

    def close_session(self, camera):
        self._track(); self.session_open = False

    def device_name(self, camera):
        return "Canon EOS Rebel T7"

    def set_uint32(self, camera, prop_id, value):
        self._track(); self.props[prop_id] = value

    def set_capacity(self, camera):
        self._track(); self.props["capacity"] = True

    def send_command(self, camera, command, param):
        self._track()
        self.commands.append((command, param))
        if command == kEdsCameraCommand_TakePicture:
            if self.af_fail:
                raise EdsError(EDS_ERR_TAKE_PICTURE_AF_NG, "EdsSendCommand")
            if self.take_picture_error is not None:
                raise EdsError(self.take_picture_error, "EdsSendCommand")
            self.pending_events.append((kEdsObjectEvent_DirItemRequestTransfer, "ITEM-1"))
        elif command == kEdsCameraCommand_PressShutterButton and param == 3:
            self.pending_events.append((kEdsObjectEvent_DirItemRequestTransfer, "ITEM-2"))

    def set_object_handler(self, camera, handler):
        self.object_handler = handler

    def set_state_handler(self, camera, handler):
        self.state_handler = handler

    def download_evf(self, camera):
        self._track()
        self.evf_calls += 1
        if self.props.get(kEdsPropID_Evf_OutputDevice) != 2:
            raise EdsError(0xA102, "EdsDownloadEvfImage")
        if self.evf_calls <= self.evf_warmup_calls:
            return None  # EDS_ERR_OBJECT_NOTREADY while live view spins up
        return EVF

    def download_item(self, item):
        self._track()
        return JPEG + item.encode()

    def release(self, ref):
        self.released.append(ref)

    def get_event(self):
        self._track()
        while self.pending_events:
            event, item = self.pending_events.pop(0)
            self.object_handler(event, item)


@pytest.fixture
def api():
    return FakeEdsApi()


@pytest.fixture
def camera(api):
    cam = EdsdkCamera(api=api, capture_timeout=2, evf_start_timeout=2)
    cam.open()
    yield cam
    cam.close()


def test_open_configures_camera_and_reports_model(camera, api):
    assert api.initialized and api.session_open
    assert api.props[kEdsPropID_SaveTo] == kEdsSaveTo_Host
    assert api.props["capacity"] is True
    assert camera.info() == {"adapter": "edsdk", "model": "Canon EOS Rebel T7"}


def test_all_sdk_calls_happen_on_one_worker_thread(camera, api):
    camera.preview_frame()
    camera.capture()
    assert len(api.thread_ids) == 1
    assert threading.get_ident() not in api.thread_ids


def test_preview_enables_live_view_and_waits_for_first_frame(camera, api):
    assert camera.preview_frame() == EVF
    assert api.props[kEdsPropID_Evf_OutputDevice] == 2
    assert api.evf_calls == 3  # two not-ready polls, then a frame
    assert camera.preview_frame() == EVF


def test_capture_downloads_photo_delivered_by_event(camera, api):
    assert camera.capture() == JPEG + b"ITEM-1"
    assert api.commands[-1] == (kEdsCameraCommand_TakePicture, 0)
    assert "ITEM-1" in api.released


def test_capture_falls_back_to_shutter_button_command():
    api = FakeEdsApi(take_picture_error=0x0060)  # EDS_ERR_INVALID_PARAMETER on newer bodies
    cam = EdsdkCamera(api=api, capture_timeout=2)
    cam.open()
    try:
        assert cam.capture() == JPEG + b"ITEM-2"
        assert (kEdsCameraCommand_PressShutterButton, 3) in api.commands
        assert (kEdsCameraCommand_PressShutterButton, 0) in api.commands
    finally:
        cam.close()


def test_autofocus_failure_is_explained():
    api = FakeEdsApi(af_fail=True)
    cam = EdsdkCamera(api=api, capture_timeout=2)
    cam.open()
    try:
        with pytest.raises(CameraError, match="Autofocus failed"):
            cam.capture()
    finally:
        cam.close()


def test_no_camera_raises_clear_error():
    cam = EdsdkCamera(api=FakeEdsApi(cameras=0))
    with pytest.raises(CameraError, match="No Canon camera found"):
        cam.open()


def test_close_turns_off_live_view_and_terminates(api):
    cam = EdsdkCamera(api=api, evf_start_timeout=2)
    cam.open()
    cam.preview_frame()
    cam.close()
    assert api.props[kEdsPropID_Evf_OutputDevice] == 0
    assert api.session_open is False
    assert api.initialized is False
    assert "CAM" in api.released
    with pytest.raises(CameraError):
        cam.preview_frame()


def test_shutdown_event_marks_camera_disconnected(camera, api):
    # Deliver a state event via the registered handler, as EdsGetEvent would.
    api.state_handler(kEdsStateEvent_Shutdown, 0)
    deadline = time.monotonic() + 2
    while camera._thread is not None and camera._thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    with pytest.raises(CameraError, match="disconnected"):
        camera.preview_frame()


def test_capture_timeout_when_no_event_arrives():
    api = FakeEdsApi()
    api.send_command = lambda camera, command, param: api.commands.append((command, param))  # never raises an event
    cam = EdsdkCamera(api=api, capture_timeout=0.3)
    cam.open()
    try:
        with pytest.raises(CameraError, match="did not deliver"):
            cam.capture()
    finally:
        cam.close()


def test_ctypes_api_refuses_non_windows():
    import sys
    if sys.platform.startswith("win"):
        pytest.skip("Windows: would try to load the real DLL")
    from booth.cameras.edsdk_camera import CtypesEdsApi
    with pytest.raises(CameraError, match="only works on Windows"):
        CtypesEdsApi(None)
