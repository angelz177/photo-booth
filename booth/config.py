"""Booth configuration, persisted as JSON in the data directory."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Layout = Literal["strip", "single", "grid"]

# Number of photos each layout needs.
LAYOUT_SHOTS: dict[str, int] = {"strip": 3, "single": 1, "grid": 4}


class BoothConfig(BaseModel):
    event_name: str = "My Event"
    layout: Layout = "strip"
    countdown_seconds: int = Field(3, ge=1, le=10)
    shot_delay_seconds: float = Field(1.5, ge=0, le=10)
    mirror_preview: bool = True
    footer_text: str = ""
    review_timeout_seconds: int = Field(45, ge=5, le=600)
    print_enabled: bool = False
    printer_name: str = ""
    print_copies: int = Field(1, ge=1, le=5)
    # Driver paper-size names applied per job (Windows: DEVMODE form name,
    # CUPS: -o media=). Empty means the printer's default. The strip default
    # is the Fujifilm ASK-300 / DNP name for one 4x6 cut into two 2x6 strips.
    paper_size_strip: str = "6x2inchx2 Type1"
    paper_size_sheet: str = ""
    admin_pin: str = "1234"
    # When set, QR codes point here (e.g. a public tunnel) instead of the
    # address the booth screen used to reach the service.
    share_base_url: str = ""

    @property
    def shots(self) -> int:
        return LAYOUT_SHOTS[self.layout]


class ConfigStore:
    """Thread-safe load/save of BoothConfig at <data_dir>/config.json."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "config.json"
        self._lock = threading.Lock()
        self._config = self._load()

    def _load(self) -> BoothConfig:
        if self.path.exists():
            try:
                return BoothConfig.model_validate_json(self.path.read_text())
            except Exception:  # corrupt file: start fresh rather than crash the booth
                pass
        return BoothConfig()

    def get(self) -> BoothConfig:
        with self._lock:
            return self._config

    def set(self, config: BoothConfig) -> BoothConfig:
        with self._lock:
            self._config = config
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(config.model_dump(), indent=2))
            return config
