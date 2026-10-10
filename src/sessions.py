"""One live CourtReserve session per facility."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import db, secrets
from .courtreserve import CourtReserveClient, CourtReserveError, EventsContext, LoginError


@dataclass
class Session:
    facility_id: int
    client: CourtReserveClient
    status: str = "unknown"  # live | down | unknown
    checked_at: datetime | None = None
    error: str | None = None
    events: EventsContext | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class Registry:
    def __init__(self) -> None:
        self._sessions: dict[int, Session] = {}
        self._lock = threading.Lock()

    def get(self, facility_id: int) -> Session | None:
        with self._lock:
            return self._sessions.get(facility_id)

    def put(self, facility_id: int, client: CourtReserveClient, status: str = "live") -> Session:
        sess = Session(facility_id=facility_id, client=client, status=status,
                       checked_at=datetime.now(timezone.utc))
        with self._lock:
            self._sessions[facility_id] = sess
        return sess

    def drop(self, facility_id: int) -> None:
        with self._lock:
            self._sessions.pop(facility_id, None)

    def status_of(self, facility_id: int) -> dict:
        sess = self.get(facility_id)
        if not sess:
            return {"status": "unknown", "checked_at": None, "error": None}
        return {
            "status": sess.status,
            "checked_at": sess.checked_at.isoformat() if sess.checked_at else None,
            "error": sess.error,
        }

    def ensure(self, facility: dict) -> Session:
        fid = int(facility["id"])
        sess = self.get(fid)
        if sess is None:
            sess = self.put(fid, CourtReserveClient(int(facility["org_id"])), status="unknown")
        with sess.lock:
            if sess.status == "live" and sess.client.is_logged_in():
                sess.checked_at = datetime.now(timezone.utc)
                return sess
            password = secrets.get_password(int(facility["org_id"]), facility["username"])
            if not password:
                sess.status, sess.error = "down", "no password in keychain"
                raise LoginError("no password stored for this facility; remove it and add it again")
            try:
                sess.client = CourtReserveClient(int(facility["org_id"]))
                sess.client.login(facility["username"], password)
                info = sess.client.facility_info()
            except (LoginError, CourtReserveError) as e:
                sess.status, sess.error = "down", str(e)
                sess.checked_at = datetime.now(timezone.utc)
                db.add_log("error", "session", f"re-login failed: {e}", fid)
                raise
            sess.status, sess.error = "live", None
            sess.checked_at = datetime.now(timezone.utc)
            db.mark_verified(fid, name=info.name, timezone=info.timezone,
                             cost_type_id=info.cost_type_id, member_id=info.member_id)
            db.add_log("info", "session", f"signed in as {facility['username']} at {info.name}", fid)
            return sess


registry = Registry()
