"""FastAPI application: booth UI, admin, gallery and the camera API."""

from __future__ import annotations

import asyncio
import html
import io
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import qrcode
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .cameras import make_camera
from .cameras.base import CameraError
from .compose import compose, save_jpeg
from .config import LAYOUT_SHOTS, BoothConfig, ConfigStore
from .manager import CameraManager
from .printing import PrintError, list_printers, print_file
from .storage import Session, SessionStore

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


def create_app(camera: str = "mock", data_dir: Path | str = "data", **camera_kwargs) -> FastAPI:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "overlays").mkdir(exist_ok=True)

    config_store = ConfigStore(data_dir)
    sessions = SessionStore(data_dir)
    manager = CameraManager(make_camera(camera, **camera_kwargs))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        manager.start()
        try:
            yield
        finally:
            manager.stop()

    app = FastAPI(title="Photo Booth", version=__version__, lifespan=lifespan)
    app.state.manager = manager
    app.state.config_store = config_store
    app.state.sessions = sessions
    app.state.data_dir = data_dir

    app.mount("/photos", StaticFiles(directory=sessions.root), name="photos")

    # -- pages -------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    async def booth_page():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/admin", include_in_schema=False)
    @app.get("/admin.html", include_in_schema=False)
    async def admin_page():
        return FileResponse(STATIC_DIR / "admin.html")

    @app.get("/gallery", include_in_schema=False)
    @app.get("/gallery.html", include_in_schema=False)
    async def gallery_page():
        return FileResponse(STATIC_DIR / "gallery.html")

    @app.get("/s/{session_id}", include_in_schema=False, response_class=HTMLResponse)
    async def share_page(session_id: str):
        session = _get_session(session_id)
        if session.share is None:
            raise HTTPException(404, "Photo is still being processed")
        src = f"/photos/{session.id}/{session.share}"
        title = html.escape(session.event_name)
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 64 64%27%3E%3Crect width=%2764%27 height=%2764%27 rx=%2714%27 fill=%27%23ff4f7b%27/%3E%3Ccircle cx=%2732%27 cy=%2734%27 r=%2714%27 fill=%27none%27 stroke=%27%23fff%27 stroke-width=%276%27/%3E%3Crect x=%2722%27 y=%2712%27 width=%2720%27 height=%278%27 rx=%273%27 fill=%27%23fff%27/%3E%3C/svg%3E">
<link rel="stylesheet" href="/css/booth.css"></head>
<body class="share"><main>
<h1>{title}</h1>
<img src="{src}" alt="Your photo">
<a class="btn" href="{src}" download="{session.id}.jpg">Save photo</a>
</main></body></html>"""

    # -- status & config ---------------------------------------------------

    @app.get("/api/status")
    async def status():
        config = config_store.get()
        return {
            "version": __version__,
            "camera": manager.status(),
            "config": config.model_dump(exclude={"admin_pin"}),
            "shots": config.shots,
            "layouts": LAYOUT_SHOTS,
        }

    @app.get("/api/config")
    async def get_config():
        return config_store.get().model_dump()

    @app.put("/api/config")
    async def put_config(config: BoothConfig):
        return config_store.set(config).model_dump()

    @app.post("/api/admin/verify")
    async def verify_pin(body: dict):
        # Kiosk convenience only: keeps guests out of settings, not a security boundary.
        return {"ok": str(body.get("pin", "")) == config_store.get().admin_pin}

    @app.get("/api/printers")
    async def printers():
        return {"printers": await asyncio.to_thread(list_printers)}

    # -- live view ---------------------------------------------------------

    @app.get("/api/live")
    async def live():
        return StreamingResponse(
            manager.mjpeg(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-cache, no-store", "Pragma": "no-cache"},
        )

    @app.get("/api/live/frame.jpg")
    async def live_frame():
        # Nudge the preview thread, then wait briefly for a frame.
        gen = manager.mjpeg(max_frames=1)
        try:
            await asyncio.wait_for(gen.__anext__(), timeout=3)
        except (StopAsyncIteration, asyncio.TimeoutError):
            pass
        finally:
            await gen.aclose()
        frame = manager.latest_frame()
        if frame is None:
            raise HTTPException(503, manager.status().get("error") or "No frame yet")
        return Response(frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # -- sessions ----------------------------------------------------------

    def _get_session(session_id: str) -> Session:
        session = sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "Unknown session")
        return session

    def _session_json(session: Session, request: Request | None = None) -> dict:
        data = session.to_dict()
        base = f"/photos/{session.id}"
        data["shot_urls"] = [f"{base}/{name}" for name in session.shots]
        data["composite_url"] = f"{base}/{session.composite}" if session.composite else None
        data["share_url"] = f"{base}/{session.share}" if session.share else None
        data["qr_url"] = f"/api/sessions/{session.id}/qr.png"
        if request is not None:
            data["share_page_url"] = _share_page_url(request, session)
        return data

    def _share_page_url(request: Request, session: Session) -> str:
        base = config_store.get().share_base_url.rstrip("/") or str(request.base_url).rstrip("/")
        return f"{base}/s/{session.id}"

    @app.post("/api/sessions", status_code=201)
    async def create_session(request: Request):
        config = config_store.get()
        session = sessions.create(config.event_name, config.layout, config.shots)
        return _session_json(session, request)

    @app.get("/api/sessions")
    async def list_sessions(request: Request, event: str | None = None, limit: int = 100):
        if event is None:
            event = config_store.get().event_name
        return [_session_json(s, request) for s in sessions.list(event or None, limit)]

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str, request: Request):
        return _session_json(_get_session(session_id), request)

    @app.post("/api/sessions/{session_id}/capture")
    async def capture(session_id: str, request: Request):
        session = _get_session(session_id)
        if len(session.shots) >= session.shots_required:
            raise HTTPException(409, "All shots already taken")
        try:
            data = await asyncio.to_thread(manager.capture)
        except CameraError as exc:
            raise HTTPException(503, str(exc)) from exc
        name = sessions.add_shot(session, data)
        return {
            "index": len(session.shots),
            "remaining": session.shots_required - len(session.shots),
            "url": f"/photos/{session.id}/{name}",
        }

    @app.post("/api/sessions/{session_id}/finish")
    async def finish(session_id: str, request: Request):
        session = _get_session(session_id)
        if len(session.shots) < session.shots_required:
            raise HTTPException(409, f"Need {session.shots_required} shots, have {len(session.shots)}")
        config = config_store.get()
        folder = sessions.dir(session.id)
        photos = [folder / name for name in session.shots]

        def _compose():
            print_img, share_img = compose(
                session.layout, photos, session.event_name, config.footer_text, data_dir / "overlays"
            )
            save_jpeg(print_img, folder / "composite.jpg")
            if share_img is print_img:
                return "composite.jpg", "composite.jpg"
            save_jpeg(share_img, folder / "share.jpg")
            return "composite.jpg", "share.jpg"

        session.composite, session.share = await asyncio.to_thread(_compose)
        sessions.save(session)
        return _session_json(session, request)

    @app.get("/api/sessions/{session_id}/qr.png")
    async def qr(session_id: str, request: Request):
        session = _get_session(session_id)
        img = qrcode.make(_share_page_url(request, session), box_size=8, border=2)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/sessions/{session_id}/print")
    async def print_session(session_id: str):
        session = _get_session(session_id)
        config = config_store.get()
        if not config.print_enabled:
            raise HTTPException(400, "Printing is disabled in settings")
        if session.composite is None:
            raise HTTPException(409, "Session has no composite yet")
        path = sessions.dir(session.id) / session.composite
        try:
            job = await asyncio.to_thread(print_file, path, config.printer_name, config.print_copies)
        except PrintError as exc:
            raise HTTPException(503, str(exc)) from exc
        session.printed += config.print_copies
        sessions.save(session)
        return {"ok": True, "job": job, "printed": session.printed}

    # Serve the UI's css/ and js/ from the root, mirroring static hosting
    # (GitHub Pages) where booth/static is the site root. Mounted last so API
    # routes and the pages above win.
    app.mount("/", StaticFiles(directory=STATIC_DIR), name="ui")

    return app
