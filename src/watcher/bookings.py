"""Pull the member's bookings and adopt waitlists the app didn't create."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .. import db
from ..courtreserve import Booking, LoginError
from ..sessions import registry
from . import runner

BOOKINGS_TTL = timedelta(minutes=10)
LOOKAHEAD = timedelta(days=35)
ACTIVE = ("armed", "preflight", "firing", "watching")
COVERING = ACTIVE + ("done", "done_dry", "disarmed", "failed", "blocked")


def fetch(fac: dict, force: bool = False) -> list[Booking]:
    sess = registry.ensure(fac)
    now = datetime.now(timezone.utc)
    if not force and sess.bookings_at and now - sess.bookings_at < BOOKINGS_TTL:
        return sess.bookings
    tok = sess.bookings_token
    if tok is None or tok.expires_at - now < timedelta(minutes=2):
        runner.pace(int(fac["id"]))
        tok = sess.bookings_token = sess.client.bookings_token()
    runner.pace(int(fac["id"]))
    today = date.today()
    try:
        rows = sess.client.my_bookings(tok, today, today + LOOKAHEAD, fac.get("timezone"))
    except LoginError:
        sess.bookings_token = None
        raise
    sess.bookings, sess.bookings_at = rows, now
    return rows


def sync(fac: dict, force: bool = False) -> dict:
    """Refresh bookings and reconcile targets: adopt manual waitlists, close targets that got booked."""
    fid = int(fac["id"])
    rows = fetch(fac, force=force)
    targets = db.list_targets(fid)
    adopted, closed = [], []
    for b in rows:
        if b.cancelled or b.start < datetime.now(b.start.tzinfo):
            continue
        mine = [t for t in targets if _covers(t, b)]
        if b.registered and not b.waitlisted:
            for t in mine:
                if t["status"] in ACTIVE:
                    db.update_target(int(t["id"]), status="done", result="booked on CourtReserve")
                    db.add_log("info", "sync", f"#{t['id']} closed: already booked", fid)
                    closed.append(int(t["id"]))
            continue
        if b.waitlisted and not any(t["status"] in COVERING for t in mine):
            label = f"manage waitlist · {(b.name or b.type_name).lower()} · {b.start:%a %b %-d %H:%M}"
            params = {"date": b.start.date().isoformat(), "start": b.start.strftime("%H:%M"), "end": b.end.strftime("%H:%M"),
                      "reservation_id": b.reservation_id, "adopted": True}
            if b.event_id:
                params.update(event_id=b.event_id, event_name=b.name)
                kind = "event_spot"
            else:
                params.update(type_name=b.type_name, duration=int((b.end - b.start).total_seconds() // 60))
                kind = "court_spot"
            t = db.insert_target(fid, kind, label, None, params, status="watching", dry_run=True)
            db.add_log("info", "sync", f"#{t['id']} adopted your waitlist: {label}", fid)
            adopted.append(int(t["id"]))
    return {"bookings": rows, "adopted": adopted, "closed": closed}


def _covers(t: dict, b: Booking) -> bool:
    p = t["params"]
    if p.get("reservation_id") and int(p["reservation_id"]) == b.reservation_id:
        return True
    if p.get("date") != b.start.date().isoformat() or p.get("start") != b.start.strftime("%H:%M"):
        return False
    if b.event_id:
        return bool(p.get("event_id") and int(p["event_id"]) == b.event_id) or \
            " ".join((p.get("event_name") or "").lower().split()) == " ".join(b.name.lower().split())
    return t["kind"].startswith("court")


def view(b: Booking) -> dict:
    return {"reservation_id": b.reservation_id, "event_id": b.event_id, "name": b.name or b.type_name,
            "type": b.type_name, "start": b.start.isoformat(timespec="minutes"), "end": b.end.isoformat(timespec="minutes"),
            "courts": b.courts, "registered": b.registered, "waitlisted": b.waitlisted, "upcoming": b.upcoming,
            "cancelled": b.cancelled, "button": b.button, "members": b.members}
