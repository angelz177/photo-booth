import asyncio
import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from booth.app import create_app
from booth.cameras.mock import MockCamera
from booth.compose import LAYOUTS, compose
from booth.manager import CameraManager

JPEG = b"\xff\xd8"
PNG = b"\x89PNG"


@pytest.fixture
def client(tmp_path):
    app = create_app(camera="mock", data_dir=tmp_path)
    with TestClient(app) as c:
        wait_connected(c)
        yield c


def wait_connected(client: TestClient, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get("/api/status").json()
        if status["camera"]["connected"]:
            return status
        time.sleep(0.05)
    raise AssertionError("camera never connected")


def image_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        return img.size


def test_status_reports_mock_camera(client):
    status = client.get("/api/status").json()
    assert status["camera"]["adapter"] == "mock"
    assert status["camera"]["connected"] is True
    assert status["shots"] == 3
    assert "admin_pin" not in status["config"]


def test_config_roundtrip_persists(client, tmp_path):
    config = client.get("/api/config").json()
    config.update(event_name="Ana & Luis", layout="grid", countdown_seconds=5, print_enabled=True)
    assert client.put("/api/config", json=config).status_code == 200
    assert (tmp_path / "config.json").exists()
    status = client.get("/api/status").json()
    assert status["config"]["event_name"] == "Ana & Luis"
    assert status["shots"] == 4


def test_config_rejects_bad_values(client):
    config = client.get("/api/config").json()
    config["countdown_seconds"] = 99
    assert client.put("/api/config", json=config).status_code == 422


def test_pin_verify(client):
    assert client.post("/api/admin/verify", json={"pin": "1234"}).json()["ok"] is True
    assert client.post("/api/admin/verify", json={"pin": "0000"}).json()["ok"] is False


def test_full_session_flow(client, tmp_path):
    session = client.post("/api/sessions").json()
    assert session["shots_required"] == 3
    sid = session["id"]

    for i in range(3):
        r = client.post(f"/api/sessions/{sid}/capture")
        assert r.status_code == 200, r.text
        assert r.json()["index"] == i + 1
        shot = client.get(r.json()["url"])
        assert shot.status_code == 200 and shot.content.startswith(JPEG)
    assert client.post(f"/api/sessions/{sid}/capture").status_code == 409

    done = client.post(f"/api/sessions/{sid}/finish").json()
    assert done["composite"] == "composite.jpg" and done["share"] == "share.jpg"
    assert done["share_page_url"].endswith(f"/s/{sid}")

    composite = client.get(done["composite_url"]).content
    share = client.get(done["share_url"]).content
    assert image_size(composite) == (1200, 1800)
    assert image_size(share) == (600, 1800)

    qr = client.get(done["qr_url"])
    assert qr.status_code == 200 and qr.content.startswith(PNG)

    page = client.get(f"/s/{sid}")
    assert page.status_code == 200 and done["share_url"] in page.text

    listed = client.get("/api/sessions").json()
    assert [s["id"] for s in listed] == [sid]


def test_finish_requires_all_shots(client):
    sid = client.post("/api/sessions").json()["id"]
    client.post(f"/api/sessions/{sid}/capture")
    assert client.post(f"/api/sessions/{sid}/finish").status_code == 409
    # unfinished sessions stay out of the gallery
    assert client.get("/api/sessions").json() == []


def test_single_layout_session(client):
    config = client.get("/api/config").json()
    config["layout"] = "single"
    client.put("/api/config", json=config)
    sid = client.post("/api/sessions").json()["id"]
    client.post(f"/api/sessions/{sid}/capture")
    done = client.post(f"/api/sessions/{sid}/finish").json()
    assert done["share"] == "composite.jpg"
    assert image_size(client.get(done["share_url"]).content) == (1200, 1800)


def test_print_disabled_by_default(client):
    sid = client.post("/api/sessions").json()["id"]
    assert client.post(f"/api/sessions/{sid}/print").status_code == 400


def test_unknown_session_404(client):
    assert client.get("/api/sessions/nope").status_code == 404
    assert client.get("/s/nope").status_code == 404


def test_live_frame_endpoint(client):
    r = client.get("/api/live/frame.jpg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content.startswith(JPEG)
    assert image_size(r.content) == (960, 640)


def test_mjpeg_stream_chunks():
    manager = CameraManager(MockCamera(), preview_fps=30)
    manager.start()
    try:
        deadline = time.monotonic() + 5
        while not manager.connected and time.monotonic() < deadline:
            time.sleep(0.05)
        assert manager.connected

        async def take_two():
            return [chunk async for chunk in manager.mjpeg(max_frames=2)]

        chunks = asyncio.run(take_two())
    finally:
        manager.stop()
    assert len(chunks) == 2
    for chunk in chunks:
        header, _, body = chunk.partition(b"\r\n\r\n")
        assert header.startswith(b"--frame\r\nContent-Type: image/jpeg")
        assert body.startswith(JPEG)
    assert manager.status()["viewers"] == 0


def test_capture_requires_connected_camera():
    manager = CameraManager(MockCamera())
    with pytest.raises(Exception):
        manager.capture()


@pytest.fixture
def photos(tmp_path):
    paths = []
    for i in range(4):
        img = Image.new("RGB", (1920, 1280), (40 * i, 120, 200))
        path = tmp_path / f"p{i}.jpg"
        img.save(path, "JPEG")
        paths.append(path)
    return paths


@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_compose_layout_sizes(layout, photos):
    print_img, share_img = compose(layout, photos, "Test Event", "Footer")
    assert print_img.size == (1200, 1800)
    expected_share = (600, 1800) if layout == "strip" else (1200, 1800)
    assert share_img.size == expected_share


def test_compose_too_few_photos(photos):
    with pytest.raises(ValueError):
        compose("grid", photos[:2], "x")


def test_compose_overlay_is_applied(tmp_path, photos):
    overlays = tmp_path / "overlays"
    overlays.mkdir()
    overlay = Image.new("RGBA", (600, 1800), (0, 0, 0, 0))
    overlay.paste((255, 0, 0, 255), (0, 1700, 600, 1800))  # opaque red band at the bottom
    overlay.save(overlays / "strip.png")
    _, share = compose("strip", photos, "Event", overlay_dir=overlays)
    assert share.getpixel((300, 1750)) == (255, 0, 0)
    # the photo area above the band is untouched (slot 1 top-left pixel is photo colour)
    assert share.getpixel((25, 25)) != (255, 0, 0)


def test_papers_endpoint(client):
    r = client.get("/api/printers/papers", params={"printer": "ASK300"})
    assert r.status_code == 200
    assert r.json()["printer"] == "ASK300"
    assert isinstance(r.json()["papers"], list)


def test_print_uses_paper_size_for_layout(client, monkeypatch):
    import booth.app as app_module

    calls = []

    def fake_print(path, printer_name, copies, paper_size):
        calls.append((path.name, printer_name, copies, paper_size))
        return "ok"

    monkeypatch.setattr(app_module, "print_file", fake_print)
    config = client.get("/api/config").json()
    config.update(print_enabled=True, printer_name="ASK300", paper_size_strip="6x2inchx2 Type1", paper_size_sheet="6x4inch")
    client.put("/api/config", json=config)

    sid = client.post("/api/sessions").json()["id"]
    for _ in range(3):
        client.post(f"/api/sessions/{sid}/capture")
    client.post(f"/api/sessions/{sid}/finish")
    assert client.post(f"/api/sessions/{sid}/print").json()["printed"] == 1
    assert calls[-1] == ("composite.jpg", "ASK300", 1, "6x2inchx2 Type1")

    config["layout"] = "single"
    client.put("/api/config", json=config)
    sid = client.post("/api/sessions").json()["id"]
    client.post(f"/api/sessions/{sid}/capture")
    client.post(f"/api/sessions/{sid}/finish")
    client.post(f"/api/sessions/{sid}/print")
    assert calls[-1][3] == "6x4inch"
