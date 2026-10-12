from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import jsonify, request

from ... import db
from ...courtreserve import CourtReserveError, LoginError, Scheduler, Slot
from ...secrets import SecretStoreError
from ...sessions import registry
from ...watcher import runner
from .common import api, day_arg, err, facility_or_404

HORIZON_TTL = timedelta(minutes=5)


def schedulers_for(fac: dict, refresh: bool) -> list[dict]:
    rows = db.list_schedulers(int(fac["id"]))
    if rows and not refresh:
        return rows
    found = registry.ensure(fac).client.schedulers()
    rows = db.replace_schedulers(int(fac["id"]), [
        {"scheduler_id": s.id, "name": s.name, "cost_type_id": s.cost_type_id, "timezone": s.timezone,
         "min_interval": s.min_interval, "open_time": s.open_time, "close_time": s.close_time} for s in found
    ])
    if found:
        db.mark_verified(int(fac["id"]), cost_type_id=found[0].cost_type_id, timezone=found[0].timezone)
    db.add_log("info", "courts", f"found {len(found)} court groups: " + ", ".join(s.name for s in found), int(fac["id"]))
    return rows


def sched_from_row(row: dict) -> Scheduler:
    return Scheduler(id=int(row["scheduler_id"]), name=row["name"], cost_type_id=row.get("cost_type_id"),
                     timezone=row.get("timezone"), min_interval=int(row.get("min_interval") or 60))


def horizon(fac: dict, rows: list[dict], sess) -> tuple[datetime | None, timedelta | None]:
    """The member's booking horizon as (max bookable instant, window length)."""
    if not rows:
        return None, None
    now = datetime.now(ZoneInfo(rows[0].get("timezone") or "UTC"))
    checked = fac.get("horizon_at")
    if fac.get("horizon_max") and checked and now - datetime.fromisoformat(checked) < HORIZON_TTL:
        hi = datetime.fromisoformat(fac["horizon_max"])
        lo = datetime.fromisoformat(fac["horizon_min"]) if fac.get("horizon_min") else datetime.fromisoformat(checked)
        return hi, hi - lo
    lo, hi = sess.client.booking_horizon(int(rows[0]["scheduler_id"]))
    if not hi:
        return None, None
    old = fac.get("horizon_max")
    db.update_facility(int(fac["id"]), horizon_min=lo.isoformat() if lo else None,
                       horizon_max=hi.isoformat(), horizon_at=now.isoformat())
    if not old or datetime.fromisoformat(old).date() != hi.date():
        db.add_log("info", "courts", f"bookable through {hi:%a %b %d} ({(hi.date() - now.date()).days} days out)", int(fac["id"]))
    return hi, hi - (lo or now)


def merge_slots(raw: list[Slot], day: date, close: str | None, member_id: int | None,
                hi: datetime | None, window: timedelta | None, now: datetime) -> list[dict]:
    """Fold the feed's court and waitlist rows into one record per hour."""
    merged: dict[str, dict] = {}
    for sl in raw:
        if sl.start.date() != day:
            continue
        key = sl.start.strftime("%H:%M")
        if close and key >= close:
            continue
        opens = None if hi is None or sl.start <= hi else sl.start - window
        cur = merged.setdefault(key, {
            "start": key, "end": sl.end.strftime("%H:%M"), "available": 0, "court_ids": [],
            "past": sl.is_past, "closed": sl.is_closed, "waitlist": None, "waitlistable": False, "waitlisted": False,
            "court_type_id": sl.court_type_id,
            "open": opens is None or now >= opens, "opens_at": opens.isoformat(timespec="minutes") if opens else None,
        })
        if (sl.court_type or "").upper().startswith("WAITLIST"):
            cur["waitlist"] = sl.waitlist_count
            cur["waitlistable"] = not sl.is_closed and not sl.is_past
            cur["waitlisted"] = bool(member_id) and member_id in sl.member_ids
        elif sl.court_type or "court_type" not in cur:
            cur["available"] = sl.available
            cur["court_ids"] = sl.court_ids
            cur["court_type"] = sl.court_type
    return [merged[k] for k in sorted(merged)]


@api.get("/facilities/<int:facility_id>/schedulers")
def schedulers(facility_id: int):
    fac, missing = facility_or_404(facility_id)
    if missing:
        return missing
    try:
        rows = schedulers_for(fac, refresh=request.args.get("refresh") == "1")
    except (LoginError, CourtReserveError, SecretStoreError) as e:
        return err(str(e), 502)
    return jsonify({"ok": True, "schedulers": rows})


@api.get("/facilities/<int:facility_id>/slots")
def slots(facility_id: int):
    fac, missing = facility_or_404(facility_id)
    if missing:
        return missing
    try:
        day = day_arg()
    except ValueError:
        return err("date must be YYYY-MM-DD")
    member_id = int(fac["member_id"]) if fac.get("member_id") else None
    try:
        rows = schedulers_for(fac, refresh=False)
        sess = registry.ensure(fac)
        hi, window = horizon(fac, rows, sess)
        tz = ZoneInfo(rows[0].get("timezone") or "UTC") if rows else ZoneInfo("UTC")
        now = datetime.now(tz)
        if hi and window:
            hi = now + window
        out = []
        for row in rows:
            if row["name"].lower().startswith("book a coach"):
                continue
            sched = sched_from_row(row)
            items = merge_slots(sess.client.read_slots(sched, day), day, row.get("close_time"), member_id, hi, window, now)
            count = max([len(i["court_ids"]) for i in items] + [int(row["court_count"] or 0)])
            if count > int(row["court_count"] or 0):
                db.bump_court_count(int(row["id"]), count)
            out.append({"scheduler_id": sched.id, "name": sched.name, "court_count": count,
                        "open_time": row.get("open_time"), "close_time": row.get("close_time"), "slots": items})
    except LoginError as e:
        registry.expire(facility_id)
        db.add_log("error", "courts", f"slots {day}: {e}", facility_id)
        return err(str(e), 502)
    except (CourtReserveError, SecretStoreError) as e:
        db.add_log("error", "courts", f"slots {day}: {e}", facility_id)
        return err(str(e), 502)
    return jsonify({"ok": True, "date": day.isoformat(), "fetched_at": now.isoformat(timespec="seconds"),
                    "timezone": str(tz), "horizon_max": hi.isoformat() if hi else None, "schedulers": out})


@api.get("/facilities/<int:facility_id>/reservation-types")
def reservation_types(facility_id: int):
    fac, missing = facility_or_404(facility_id)
    if missing:
        return missing
    try:
        day = day_arg()
        sid = int(request.args["scheduler_id"])
        start = request.args["start"]
        ct = int(request.args.get("court_type_id") or 9)
    except (ValueError, KeyError):
        return err("date, scheduler_id, start required")
    try:
        sess = registry.ensure(fac)
        runner.pace(facility_id)
        types = sess.client.reservation_types(sid, day, start, runner.add_minutes(start, 60), ct)
        out = []
        for t in types:
            runner.pace(facility_id)
            durations = sess.client.duration_options(sid, t.id, day, start, ct) or [t.min_time or 60]
            out.append({"id": t.id, "name": t.name, "durations": durations, "min_players": t.min_players, "max_players": t.max_players})
    except (LoginError, CourtReserveError, SecretStoreError) as e:
        return err(str(e), 502)
    return jsonify({"ok": True, "types": out})


@api.get("/facilities/<int:facility_id>/members")
def members(facility_id: int):
    fac, missing = facility_or_404(facility_id)
    if missing:
        return missing
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"ok": True, "members": []})
    try:
        sess = registry.ensure(fac)
        runner.pace(facility_id)
        found = sess.client.search_members(q, fac.get("cost_type_id"), None, request.args.get("scheduler_id", type=int))
    except (LoginError, CourtReserveError, SecretStoreError) as e:
        return err(str(e), 502)
    return jsonify({"ok": True, "members": [{"org_member_id": m.org_member_id, "member_id": m.member_id, "name": m.name} for m in found[:20]]})
