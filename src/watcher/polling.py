"""Spot watchers: poll until a full event or court frees up, then hand off to the runner."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .. import db
from ..courtreserve import CourtReserveError, LoginError, Scheduler
from ..sessions import registry
from . import runner
from .runner import add_minutes, attempt, pace

READS_PER_DAY = 400


def _cadence(left: timedelta) -> timedelta:
    if left <= timedelta(hours=2):
        return timedelta(seconds=30)
    if left <= timedelta(hours=24):
        return timedelta(minutes=2)
    return timedelta(minutes=10)


def _schedule_next(target_id: int, now: datetime, start: datetime) -> None:
    t = db.get_target(target_id)
    if t:
        p = dict(t["params"])
        p["next_poll_at"] = (now + _cadence(start - now)).isoformat(timespec="seconds")
        db.update_target(target_id, params=p)


def _norm(name: str) -> str:
    return " ".join((name or "").lower().split())


def _match_event(found: list, p: dict):
    if p.get("details_path"):
        hit = next((e for e in found if e.details_path == p["details_path"]), None)
        if hit:
            return hit
    if p.get("reservation_id") and not p.get("adopted"):
        hit = next((e for e in found if e.reservation_id == int(p["reservation_id"])), None)
        if hit:
            return hit
    want = _norm(p.get("event_name") or "")
    return next((e for e in found if _norm(e.name) == want and e.start == p.get("start")), None)


def _start_of(t: dict, tz: ZoneInfo) -> datetime:
    p = t["params"]
    return datetime.fromisoformat(p["date"] + "T" + (p.get("start") or "00:00")).replace(tzinfo=tz)


def poll(target_id: int) -> None:
    t = db.get_target(target_id)
    if not t or t["status"] != "watching":
        return
    fac = db.get_facility(int(t["facility_id"]))
    tz = ZoneInfo(fac.get("timezone") or "UTC")
    now = datetime.now(tz)
    start = _start_of(t, tz)
    if now >= start:
        db.update_target(target_id, status="expired", result="already started")
        attempt(t, "watch", "expired")
        return
    reads = sum(1 for a in db.list_attempts(target_id) if a["step"] == "watch" and a["ts"][:10] == now.date().isoformat())
    if reads >= READS_PER_DAY:
        db.update_target(target_id, status="failed", result="daily read budget spent")
        return
    try:
        sess = registry.ensure(fac)
        if t["kind"] == "court_spot":
            _poll_court(t, fac, sess, start)
        else:
            _poll_event(t, fac, sess, start)
    except (LoginError, CourtReserveError) as e:
        attempt(t, "watch", "error", note=str(e))
    if (db.get_target(target_id) or {}).get("status") == "watching":
        _schedule_next(target_id, now, start)


def _poll_event(t: dict, fac: dict, sess, start: datetime) -> None:
    target_id, p, fid = int(t["id"]), t["params"], int(fac["id"])
    ctx = sess.events
    if ctx is None:
        pace(fid)
        ctx = sess.events = sess.client.events_context()
    pace(fid)
    found = sess.client.list_events(ctx, date.fromisoformat(p["date"]),
                                    [c.id for c in ctx.categories if "open play" in c.name.lower()], fac.get("cost_type_id"))
    ev = _match_event(found, p)
    if ev is None:
        attempt(t, "watch", "missing", note="event not in list")
        return
    label = ev.action.lower()
    if "registered" in label and "wait" not in label:
        db.update_target(target_id, status="done", result="already registered on CourtReserve")
        attempt(t, "watch", "done", note="already registered")
        return
    if label in ("", "details"):
        attempt(t, "watch", "closed", note=f"registration not open · {ev.spots}")
        return
    if not (ev.spots_left or 0) > 0:
        attempt(t, "watch", "full", note=ev.spots or ev.action)
        return
    attempt(t, "watch", "spot", note=f"{ev.spots_left} open · card says {ev.action}")
    newp = dict(p, details_path=ev.details_path)
    if newp.get("adopted") or not newp.get("number"):
        pace(fid)
        info = sess.client.resolve_event(ev.details_path)
        newp.update({k: v for k, v in info.items() if v is not None})
    db.update_target(target_id, status="armed", kind="event_register", params=newp)
    runner.run_now(target_id)


def _poll_court(t: dict, fac: dict, sess, start: datetime) -> None:
    target_id, p, fid = int(t["id"]), t["params"], int(fac["id"])
    rows = [r for r in db.list_schedulers(fid) if not r["name"].lower().startswith("book a coach")]
    if p.get("scheduler_id"):
        rows = [r for r in rows if int(r["scheduler_id"]) == int(p["scheduler_id"])]
    hit = None
    for row in rows:
        pace(fid)
        sched = Scheduler(id=int(row["scheduler_id"]), name=row["name"], cost_type_id=row.get("cost_type_id"),
                          timezone=row.get("timezone"), min_interval=int(row.get("min_interval") or 60))
        for sl in sess.client.read_slots(sched, start.date()):
            if sl.start == start and not (sl.court_type or "").upper().startswith("WAITLIST") and sl.available > 0:
                hit = (row, sl)
                break
        if hit:
            break
    if not hit:
        attempt(t, "watch", "full")
        return
    row, sl = hit
    attempt(t, "watch", "spot", note=f"{sl.available} court(s) on {row['name']}")
    newp = dict(p, scheduler_id=int(row["scheduler_id"]), scheduler_name=row["name"],
                court_type_id=sl.court_type_id or 9, court_type=sl.court_type or "Pickleball")
    if not newp.get("reservation_type_id"):
        pace(fid)
        types = sess.client.reservation_types(int(row["scheduler_id"]), start.date(), p["start"],
                                              add_minutes(p["start"], 60), sl.court_type_id or 9)
        want = (p.get("type_name") or "").strip().lower()
        chosen = next((x for x in types if x.name.lower() == want), types[0] if types else None)
        if chosen:
            newp["reservation_type_id"] = chosen.id
    db.update_target(target_id, status="armed", kind="court_reserve", params=newp)
    runner.run_now(target_id)
