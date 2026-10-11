from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, render_template, request

from .. import __version__, db, secrets
from ..courtreserve import CourtReserveClient, CourtReserveError, Event, LoginError, Scheduler, Slot, parse_org_id
from ..secrets import SecretStoreError
from ..sessions import delete_cookies, registry, save_cookies

pages = Blueprint("pages", __name__)
api = Blueprint("api", __name__)

HORIZON_TTL = timedelta(minutes=5)
EVENTS_CTX_TTL = timedelta(minutes=30)
FACILITY_FIELDS = ("id", "org_id", "name", "username", "timezone", "cost_type_id", "member_id",
                   "created_at", "verified_at", "horizon_min", "horizon_max", "horizon_at")


@pages.get("/")
def index():
    return render_template("index.html", version=__version__)


def _facility_view(f: dict) -> dict:
    out = {k: f[k] for k in FACILITY_FIELDS}
    out["session"] = registry.status_of(int(f["id"]))
    return out


def _err(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def _day_arg() -> date:
    return date.fromisoformat(request.args.get("date") or date.today().isoformat())


@api.get("/state")
def state():
    rows = db.list_facilities()
    for f in rows:
        if registry.get(int(f["id"])) is None:
            try:
                registry.ensure(f)
            except (LoginError, CourtReserveError, SecretStoreError):
                pass
    return jsonify({"ok": True, "version": __version__,
                    "facilities": [_facility_view(f) for f in rows], "log": db.read_log(limit=60)})


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

    fac = db.insert_facility(org_id=org_id, name=info.name, username=username, timezone=info.timezone,
                             cost_type_id=info.cost_type_id, member_id=info.member_id)
    registry.put(int(fac["id"]), client, status="live")
    save_cookies(client)
    db.add_log("info", "setup", f"added {info.name} (org {org_id}) as {username}", int(fac["id"]))
    return jsonify({"ok": True, "facility": _facility_view(fac)})


@api.delete("/facilities/<int:facility_id>")
def remove_facility(facility_id: int):
    fac = db.get_facility(facility_id)
    if not fac:
        return _err("no such facility", 404)
    registry.drop(facility_id)
    delete_cookies(int(fac["org_id"]))
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
        registry.ensure(fac, verify=True)
    except (LoginError, CourtReserveError, SecretStoreError) as e:
        return jsonify({"ok": False, "error": str(e), "facility": _facility_view(db.get_facility(facility_id) or fac)}), 502
    return jsonify({"ok": True, "facility": _facility_view(db.get_facility(facility_id) or fac)})


def _schedulers_for(fac: dict, refresh: bool) -> list[dict]:
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


def _sched_from_row(row: dict) -> Scheduler:
    return Scheduler(id=int(row["scheduler_id"]), name=row["name"], cost_type_id=row.get("cost_type_id"),
                     timezone=row.get("timezone"), min_interval=int(row.get("min_interval") or 60))


def _horizon(fac: dict, rows: list[dict], sess) -> tuple[datetime | None, timedelta | None]:
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


def _merge_slots(raw: list[Slot], day: date, close: str | None, member_id: int | None,
                 horizon: datetime | None, window: timedelta | None, now: datetime) -> list[dict]:
    """Fold the feed's court and waitlist rows into one record per hour."""
    merged: dict[str, dict] = {}
    for sl in raw:
        if sl.start.date() != day:
            continue
        key = sl.start.strftime("%H:%M")
        if close and key >= close:
            continue
        opens = None if horizon is None or sl.start <= horizon else sl.start - window
        cur = merged.setdefault(key, {
            "start": key, "end": sl.end.strftime("%H:%M"), "available": 0, "court_ids": [],
            "past": sl.is_past, "closed": sl.is_closed, "waitlist": None, "waitlistable": False, "waitlisted": False,
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
        day = _day_arg()
    except ValueError:
        return _err("date must be YYYY-MM-DD")
    member_id = int(fac["member_id"]) if fac.get("member_id") else None
    try:
        rows = _schedulers_for(fac, refresh=False)
        sess = registry.ensure(fac)
        horizon, window = _horizon(fac, rows, sess)
        tz = ZoneInfo(rows[0].get("timezone") or "UTC") if rows else ZoneInfo("UTC")
        now = datetime.now(tz)
        if horizon and window:
            horizon = now + window
        out = []
        for row in rows:
            if row["name"].lower().startswith("book a coach"):
                continue
            sched = _sched_from_row(row)
            items = _merge_slots(sess.client.read_slots(sched, day), day, row.get("close_time"),
                                 member_id, horizon, window, now)
            count = max([len(i["court_ids"]) for i in items] + [int(row["court_count"] or 0)])
            if count > int(row["court_count"] or 0):
                db.bump_court_count(int(row["id"]), count)
            out.append({"scheduler_id": sched.id, "name": sched.name, "court_count": count,
                        "open_time": row.get("open_time"), "close_time": row.get("close_time"), "slots": items})
    except LoginError as e:
        registry.expire(facility_id)
        db.add_log("error", "courts", f"slots {day}: {e}", facility_id)
        return _err(str(e), 502)
    except (CourtReserveError, SecretStoreError) as e:
        db.add_log("error", "courts", f"slots {day}: {e}", facility_id)
        return _err(str(e), 502)
    return jsonify({"ok": True, "date": day.isoformat(), "fetched_at": now.isoformat(timespec="seconds"),
                    "timezone": str(tz), "horizon_max": horizon.isoformat() if horizon else None, "schedulers": out})


def _events_ctx(sess, force: bool = False):
    ctx = sess.events
    if force or ctx is None or datetime.now(ZoneInfo("UTC")) - ctx.fetched_at > EVENTS_CTX_TTL:
        ctx = sess.client.events_context()
        sess.events = ctx
    return ctx


def _event_view(e: Event) -> dict:
    url = f"https://app.courtreserve.com{e.details_path}" if e.details_path.startswith("/") else e.details_path
    return {"category": e.category, "name": e.name, "when": e.when, "start": e.start, "end": e.end, "cost": e.cost,
            "spots": e.spots, "spots_left": e.spots_left, "spots_total": e.spots_total, "tags": e.tags,
            "action": e.action, "details_url": url, "reservation_id": e.reservation_id}


@api.get("/facilities/<int:facility_id>/events")
def events(facility_id: int):
    fac = db.get_facility(facility_id)
    if not fac:
        return _err("no such facility", 404)
    try:
        day = _day_arg()
    except ValueError:
        return _err("date must be YYYY-MM-DD")
    want = (request.args.get("category") or "open play").lower()
    try:
        sess = registry.ensure(fac)
        for attempt in range(2):
            ctx = _events_ctx(sess, force=attempt == 1)
            cats = list(ctx.categories) if want == "all" else [c for c in ctx.categories if want in c.name.lower()]
            try:
                found = sess.client.list_events(ctx, day, [c.id for c in cats], fac.get("cost_type_id"))
                break
            except CourtReserveError:
                if attempt == 1:
                    raise
    except LoginError as e:
        registry.expire(facility_id)
        db.add_log("error", "events", f"events {day}: {e}", facility_id)
        return _err(str(e), 502)
    except (CourtReserveError, SecretStoreError) as e:
        db.add_log("error", "events", f"events {day}: {e}", facility_id)
        return _err(str(e), 502)
    found.sort(key=lambda e: (e.start or "", e.name))
    return jsonify({"ok": True, "date": day.isoformat(), "categories": [c.name for c in cats],
                    "all_categories": [{"id": c.id, "name": c.name} for c in ctx.categories],
                    "events": [_event_view(e) for e in found]})
