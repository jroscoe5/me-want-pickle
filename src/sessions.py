"""One live CourtReserve session per facility."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.cookiejar import LWPCookieJar
from pathlib import Path

from . import config, db, secrets
from .courtreserve import CourtReserveClient, CourtReserveError, EventsContext, LoginError

VERIFY_TTL = timedelta(minutes=10)


def session_path(org_id: int) -> Path:
    return config.DATA_DIR / f"session-{org_id}.cookies"


def load_cookies(client: CourtReserveClient) -> bool:
    path = session_path(client.org_id)
    if not path.exists():
        return False
    jar = LWPCookieJar(str(path))
    try:
        jar.load(ignore_discard=True, ignore_expires=False)
    except OSError:
        return False
    for c in jar:
        client.s.cookies.set_cookie(c)
    return len(client.s.cookies) > 0


def save_cookies(client: CourtReserveClient) -> None:
    config.ensure_data_dir()
    path = session_path(client.org_id)
    jar = LWPCookieJar(str(path))
    for c in client.s.cookies:
        jar.set_cookie(c)
    jar.save(ignore_discard=True)
    os.chmod(path, 0o600)


def delete_cookies(org_id: int) -> None:
    try:
        session_path(org_id).unlink()
    except FileNotFoundError:
        pass


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
        checked = datetime.now(timezone.utc) if status == "live" else None
        sess = Session(facility_id=facility_id, client=client, status=status, checked_at=checked)
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

    def ensure(self, facility: dict, verify: bool = False) -> Session:
        fid = int(facility["id"])
        org_id = int(facility["org_id"])
        sess = self.get(fid)
        if sess is None:
            client = CourtReserveClient(org_id)
            resumed = load_cookies(client)
            sess = self.put(fid, client, status="unknown")
            if resumed:
                db.add_log("info", "session", f"resuming saved session for {facility['username']}", fid)
        with sess.lock:
            now = datetime.now(timezone.utc)
            fresh = sess.checked_at is not None and now - sess.checked_at < VERIFY_TTL
            if sess.status == "live" and fresh and not verify:
                return sess
            if sess.status in ("live", "unknown") and sess.client.is_logged_in():
                sess.status, sess.error, sess.checked_at = "live", None, now
                save_cookies(sess.client)
                return sess
            password = secrets.get_password(org_id, facility["username"])
            if not password:
                sess.status, sess.error = "down", "no password in keychain"
                raise LoginError("no password stored for this facility; remove it and add it again")
            try:
                sess.client = CourtReserveClient(org_id)
                sess.client.login(facility["username"], password)
                info = sess.client.facility_info()
            except (LoginError, CourtReserveError) as e:
                sess.status, sess.error, sess.checked_at = "down", str(e), datetime.now(timezone.utc)
                db.add_log("error", "session", f"re-login failed: {e}", fid)
                raise
            sess.status, sess.error, sess.checked_at = "live", None, datetime.now(timezone.utc)
            save_cookies(sess.client)
            db.mark_verified(fid, name=info.name, timezone=info.timezone,
                             cost_type_id=info.cost_type_id, member_id=info.member_id)
            db.add_log("info", "session", f"signed in as {facility['username']} at {info.name}", fid)
            return sess

    def expire(self, facility_id: int) -> None:
        sess = self.get(facility_id)
        if sess:
            sess.status, sess.checked_at = "unknown", None


registry = Registry()
