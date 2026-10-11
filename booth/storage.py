"""Session storage: one folder per booth session under <data_dir>/sessions."""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path


@dataclass
class Session:
    id: str
    event_name: str
    layout: str
    shots_required: int
    created_at: str
    shots: list[str] = field(default_factory=list)  # file names, in order
    composite: str | None = None  # print file name
    share: str | None = None  # share/preview file name
    printed: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class SessionStore:
    def __init__(self, data_dir: Path):
        self.root = data_dir / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def dir(self, session_id: str) -> Path:
        return self.root / session_id

    def create(self, event_name: str, layout: str, shots_required: int) -> Session:
        now = datetime.now()
        session_id = f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        session = Session(
            id=session_id,
            event_name=event_name,
            layout=layout,
            shots_required=shots_required,
            created_at=now.isoformat(timespec="seconds"),
        )
        self.dir(session_id).mkdir(parents=True)
        self.save(session)
        return session

    def save(self, session: Session) -> None:
        with self._lock:
            (self.dir(session.id) / "meta.json").write_text(
                json.dumps(session.to_dict(), indent=2)
            )

    def get(self, session_id: str) -> Session | None:
        meta = self.dir(session_id) / "meta.json"
        if not meta.exists():
            return None
        return Session(**json.loads(meta.read_text()))

    def add_shot(self, session: Session, data: bytes) -> str:
        name = f"shot-{len(session.shots) + 1}.jpg"
        (self.dir(session.id) / name).write_bytes(data)
        session.shots.append(name)
        self.save(session)
        return name

    def list(self, event_name: str | None = None, limit: int = 200) -> list[Session]:
        sessions: list[Session] = []
        for path in sorted(self.root.iterdir(), reverse=True):
            if not path.is_dir():
                continue
            session = self.get(path.name)
            if session is None or session.share is None:
                continue
            if event_name and session.event_name != event_name:
                continue
            sessions.append(session)
            if len(sessions) >= limit:
                break
        return sessions
