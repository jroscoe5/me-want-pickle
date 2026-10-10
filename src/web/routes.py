from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, render_template, request

from .. import __version__, db, secrets
from ..courtreserve import CourtReserveClient, CourtReserveError, LoginError, Scheduler, parse_org_id
from ..courtreserve.client import Event
from ..secrets import SecretStoreError
from ..sessions import registry

pages = Blueprint("pages", __name__)
api = Blueprint("api", __name__)


@pages.get("/")
def index():
    return render_template("index.html", version=__version__)


def _facility_view(f: dict) -> dict:
    out = {k: f[k] for k in ("id", "org_id", "name", "username", "timezone",
                             "cost_type_id", "member_id", "created_at", "verified_at",
                             "release_time", "horizon_min", "horizon_max", "horizon_at")}
    out["session"] = registry.status_of(int(f["id"]))
    return out


def _err(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


@api.get("/state")
def state():
    facilities = [_facility_view(f) for f in db.list_facilities()]
    return jsonify({
        "ok": True,
        "version": __version__,
        "facilities": facilities,
        "log": db.read_log(limit=60),
    })


@api.get("/log")
def log():
    after = request.args.get("after", type=int, default=0)
    fid = request.args.get("facility_id", type=int)
    return jsonify({"ok": True, "log": db.read_log(after_id=after, facility_id=fid)})


@api.post("/facilities")
def add_facility():
    body = request.get_json(silent=True) or {}
    facility_in = str(body.get("facility", "")).strip()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))

    if not facility_in or not username or not password:
        return _err("facility, username, and password are all required")
    try:
        org_id = parse_org_id(facility_in)
    except ValueError as e:
        return _err(str(e))
    if db.get_facility_by_org(org_id):
        return _err(f"facility {org_id} is already set up; remove it first to change the account")

    db.add_log("info", "setup", f"verifying {username} at facility {org_id}")
    client = CourtReserveClient(org_id)
    try:
        client.login(username, password)
        info = client.facility_info()
    except LoginError as e:
        db.add_log("error", "setup", f"login rejected for facility {org_id}: {e}")
        return _err(f"CourtReserve rejected the login: {e}", 401)
    except CourtReserveError as e:
        db.add_log("error", "setup", f"could not verify facility {org_id}: {e}")
        return _err(str(e), 502)

    try:
        secrets.set_password(org_id, username, password)
    except SecretStoreError as e:
        db.add_log("error", "setup", str(e))
        return _err(str(e), 500)

    fac = db.insert_facility(
        org_id=org_id, name=info.name, username=username, timezone=info.timezone,
        cost_type_id=info.cost_type_id, member_id=info.member_id, verified=True,
    )
    registry.put(int(fac["id"]), client, status="live")
    db.add_log("info", "setup", f"added {info.name} (org {org_id}) as {username}", int(fac["id"]))
    return jsonify({"ok": True, "facility": _facility_view(fac)})


@api.patch("/facilities/<int:facility_id>")
def update_facility(facility_id: int):
    fac = db.get_facility(facility_id)
    if not fac:
        return _err("no such facility", 404)
    body = request.get_json(silent=True) or {}
    fields = {}
    if "release_time" in body:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(body["release_time"])):
            return _err("release_time must be HH:MM")
        fields["release_time"] = body["release_time"]
    if fields:
        db.update_facility(facility_id, **fields)
        db.add_log("info", "setup", f"new day opens at {fields['release_time']}", facility_id)
    return jsonify({"ok": True, "facility": _facility_view(db.get_facility(facility_id))})


HORIZON_TTL = timedelta(minutes=5)


def _horizon(fac: dict, rows: list[dict], sess) -> tuple[datetime | None, datetime | None]:
    tzname = rows[0].get("timezone") if rows else None
    tz = ZoneInfo(tzname or "UTC")
    now = datetime.now(tz)
    checked = fac.get("horizon_at")
    if fac.get("horizon_max") and checked and now - datetime.fromisoformat(checked) < HORIZON_TTL:
        return (datetime.fromisoformat(fac["horizon_min"]) if fac.get("horizon_min") else None,
                datetime.fromisoformat(fac["horizon_max"]))
    if not rows:
        return None, None
    lo, hi = sess.client.booking_horizon(int(rows[0]["scheduler_id"]))
    if hi:
        old = fac.get("horizon_max")
        db.update_facility(int(fac["id"]), horizon_min=lo.isoformat() if lo else None,
                           horizon_max=hi.isoformat(), horizon_at=now.isoformat())
        if not old or datetime.fromisoformat(old).date() != hi.date():
            db.add_log("info", "courts", f"bookable through {hi:%a %b %d} ({(hi.date() - now.date()).days} days out)", int(fac["id"]))
    return lo, hi


def _opens_at(fac: dict, horizon_max: datetime, slot_start: datetime) -> datetime:
    days = (horizon_max.date() - datetime.now(slot_start.tzinfo).date()).days
    hh, mm = (fac.get("release_time") or "00:00").split(":")
    d = slot_start.date() - timedelta(days=days)
    return datetime.combine(d, time(int(hh), int(mm)), tzinfo=slot_start.tzinfo)





@api.delete("/facilities/<int:facility_id>")
def remove_facility(facility_id: int):
    fac = db.get_facility(facility_id)
    if not fac:
        return _err("no such facility", 404)
    registry.drop(facility_id)
    secrets.delete_password(int(fac["org_id"]), fac["username"])
    db.delete_facility(facility_id)
    db.add_log("info", "setup", f"removed {fac['name']} (org {fac['org_id']}) and its password")
    return jsonify({"ok": True})


@api.post("/facilities/<int:facility_id>/check")
def check_facility(facility_id: int):
    fac = db.get_facility(facility_id)
    if not fac:
        return _err("no such facility", 404)
    try:
        registry.ensure(fac)
    except (LoginError, CourtReserveError, SecretStoreError) as e:
        fac = db.get_facility(facility_id) or fac
        return jsonify({"ok": False, "error": str(e), "facility": _facility_view(fac)}), 502
    fac = db.get_facility(facility_id) or fac
    return jsonify({"ok": True, "facility": _facility_view(fac)})


def _schedulers_for(fac: dict, refresh: bool) -> list[dict]:
    rows = db.list_schedulers(int(fac["id"]))
    if rows and not refresh:
        return rows
    sess = registry.ensure(fac)
    found = sess.client.schedulers()
    rows = db.replace_schedulers(int(fac["id"]), [
        {"scheduler_id": s.id, "name": s.name, "cost_type_id": s.cost_type_id,
         "timezone": s.timezone, "min_interval": s.min_interval} for s in found
    ])
    if found:
        db.mark_verified(int(fac["id"]), cost_type_id=found[0].cost_type_id, timezone=found[0].timezone)
    db.add_log("info", "courts", f"found {len(found)} court groups: " + ", ".join(s.name for s in found), int(fac["id"]))
    return rows


def _sched_from_row(row: dict) -> Scheduler:
    return Scheduler(
        id=int(row["scheduler_id"]), name=row["name"], cost_type_id=row.get("cost_type_id"),
        timezone=row.get("timezone"), min_interval=int(row.get("min_interval") or 60),
    )


@api.get("/facilities/<int:facility_id>/schedulers")
def schedulers(facility_id: int):
    fac = db.get_facility(facility_id)
    if not fac:
        return _err("no such facility", 404)
    try:
        rows = _schedulers_for(fac, refresh=request.args.get("refresh") == "1")
    except (LoginError, CourtReserveError, SecretStoreError) as e:
        return _err(str(e), 502)
    return jsonify({"ok": True, "schedulers": rows})


@api.get("/facilities/<int:facility_id>/slots")
def slots(facility_id: int):
    fac = db.get_facility(facility_id)
    if not fac:
        return _err("no such facility", 404)
    try:
        day = date.fromisoformat(request.args.get("date") or date.today().isoformat())
    except ValueError:
        return _err("date must be YYYY-MM-DD")
    try:
        rows = _schedulers_for(fac, refresh=False)
        sess = registry.ensure(fac)
        lo, hi = _horizon(fac, rows, sess)
        out = []
        for row in rows:
            if row["name"].lower().startswith("book a coach"):
                continue
            sched = _sched_from_row(row)
            raw = sess.client.read_slots(sched, day)
            merged: dict[str, dict] = {}
            now = datetime.now(ZoneInfo(sched.timezone or "UTC"))
            for sl in raw:
                if sl.start.date() != day:
                    continue
                key = sl.start.strftime("%H:%M")
                if hi is None:
                    is_open, opens = True, None
                elif sl.start.date() <= hi.date():
                    is_open, opens = True, None
                else:
                    opens = _opens_at(fac, hi, sl.start)
                    is_open = now >= opens
                cur = merged.setdefault(key, {
                    "start": key, "end": sl.end.strftime("%H:%M"), "available": 0, "court_ids": [],
                    "past": sl.is_past, "closed": sl.is_closed, "waitlist": None,
                    "opens_at": opens.isoformat(timespec="minutes") if opens else None, "open": is_open,
                })
                if (sl.court_type or "").upper().startswith("WAITLIST"):
                    cur["waitlist"] = sl.waitlist_count
                elif sl.court_type or "court_type" not in cur:
                    cur["available"] = sl.available
                    cur["court_ids"] = sl.court_ids
                    cur["court_type"] = sl.court_type
            items = [merged[k] for k in sorted(merged)]
            count = max([len(i["court_ids"]) for i in items] + [int(row["court_count"] or 0)])
            if count > int(row["court_count"] or 0):
                db.bump_court_count(int(row["id"]), count)
            out.append({"scheduler_id": sched.id, "name": sched.name, "court_count": count, "slots": items})
    except (LoginError, CourtReserveError, SecretStoreError) as e:
        db.add_log("error", "courts", f"slots {day}: {e}", facility_id)
        return _err(str(e), 502)
    return jsonify({"ok": True, "date": day.isoformat(), "fetched_at": datetime.now().isoformat(timespec="seconds"),
                    "timezone": rows[0].get("timezone") if rows else None,
                    "horizon_min": lo.isoformat() if lo else None, "horizon_max": hi.isoformat() if hi else None,
                    "schedulers": out})


EVENTS_CTX_TTL = timedelta(minutes=30)


def _events_ctx(sess, force: bool = False):
    ctx = sess.events
    if force or ctx is None or datetime.now(ZoneInfo("UTC")) - ctx.fetched_at > EVENTS_CTX_TTL:
        ctx = sess.client.events_context()
        sess.events = ctx
    return ctx


def _event_view(e: Event) -> dict:
    return {
        "category": e.category, "name": e.name, "when": e.when, "start": e.start, "end": e.end, "cost": e.cost,
        "spots": e.spots, "spots_left": e.spots_left, "spots_total": e.spots_total, "tags": e.tags,
        "action": e.action, "details_url": (f"https://app.courtreserve.com{e.details_path}" if e.details_path.startswith("/") else e.details_path),
        "reservation_id": e.reservation_id,
    }


@api.get("/facilities/<int:facility_id>/events")
def events(facility_id: int):
    fac = db.get_facility(facility_id)
    if not fac:
        return _err("no such facility", 404)
    try:
        day = date.fromisoformat(request.args.get("date") or date.today().isoformat())
    except ValueError:
        return _err("date must be YYYY-MM-DD")
    want = (request.args.get("category") or "open play").lower()
    try:
        sess = registry.ensure(fac)
        ctx = _events_ctx(sess)
        cats = [c for c in ctx.categories if want in c.name.lower()] if want != "all" else list(ctx.categories)
        try:
            found = sess.client.list_events(ctx, day, [c.id for c in cats], fac.get("cost_type_id"))
        except CourtReserveError:
            ctx = _events_ctx(sess, force=True)
            cats = [c for c in ctx.categories if want in c.name.lower()] if want != "all" else list(ctx.categories)
            found = sess.client.list_events(ctx, day, [c.id for c in cats], fac.get("cost_type_id"))
    except (LoginError, CourtReserveError, SecretStoreError) as e:
        db.add_log("error", "events", f"events {day}: {e}", facility_id)
        return _err(str(e), 502)
    found.sort(key=lambda e: (e.start or "", e.name))
    return jsonify({"ok": True, "date": day.isoformat(), "categories": [c.name for c in cats],
                    "all_categories": [{"id": c.id, "name": c.name} for c in ctx.categories],
                    "events": [_event_view(e) for e in found]})
