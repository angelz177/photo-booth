"""Run the booth service: ``python -m booth --camera gphoto2``."""

from __future__ import annotations

import argparse
import logging
import os

from .cameras import ADAPTERS


def main() -> None:
    parser = argparse.ArgumentParser(prog="photo-booth", description="Photo booth camera service + UI")
    parser.add_argument(
        "--camera",
        choices=ADAPTERS,
        default=os.environ.get("BOOTH_CAMERA", "mock"),
        help="camera adapter (default: $BOOTH_CAMERA or 'mock')",
    )
    parser.add_argument("--webcam-index", type=int, default=0, help="device index for --camera webcam")
    parser.add_argument(
        "--edsdk-dir",
        default=os.environ.get("BOOTH_EDSDK_DIR", "EDSDK"),
        help="folder containing Canon's EDSDK.dll for --camera edsdk (Windows)",
    )
    parser.add_argument("--data", default=os.environ.get("BOOTH_DATA", "data"), help="data directory")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("BOOTH_PORT", "8000")))
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    import uvicorn

    from .app import create_app

    app = create_app(camera=args.camera, data_dir=args.data, webcam_index=args.webcam_index, edsdk_dir=args.edsdk_dir)
    print(f"Photo booth ({args.camera} camera) -> http://localhost:{args.port}/   admin: /admin   gallery: /gallery")
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
