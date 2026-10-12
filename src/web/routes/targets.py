from __future__ import annotations

import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import jsonify, request

from ... import db
from ...courtreserve import CourtReserveError, LoginError
from ...secrets import SecretStoreError
from ...sessions import registry
from ...watcher import bookings as bookings_sync, clock, polling, runner
from .common import api, err, facility_or_404

TARGET_KINDS = ("court_reserve", "court_waitlist", "event_register", "event_waitlist", "event_spot", "court_spot")
WATCHERS = ("event_spot", "court_spot")


def target_view(t: dict) -> dict:
    return {k: t[k] for k in ("id", "facility_id", "kind", "status", "label", "fire_at", "params", "dry_run",
                              "attempts", "result", "created_at", "updated_at")}


def _resolve_event(fac: dict, p: dict):
    sess = registry.ensure(fac)
    runner.pace(int(fac["id"]))
    info = sess.client.resolve_event(p["details_path"])
    if not info.get("event_id") or not info.get("reservation_id"):
        raise CourtReserveError("could not read the event ids from its page")
    p.update({k: v for k, v in info.items() if v is not None})


def _event_opens_at(fac: dict, p: dict) -> str | None:
    if not fac.get("horizon_max") or not fac.get("horizon_at"):
        return None
    window = datetime.fromisoformat(fac["horizon_max"]) - datetime.fromisoformat(fac["horizon_at"])
    tz = ZoneInfo(fac.get("timezone") or "UTC")
    start = datetime.fromisoformat(p["date"] + "T" + (p.get("start") or "00:00")).replace(tzinfo=tz)
    opens = start - window
    return opens.isoformat(timespec="minutes") if opens > datetime.now(tz) else None


@api.get("/facilities/<int:facility_id>/targets")
def targets(facility_id: int):
    return jsonify({"ok": True, "targets": [target_view(t) for t in db.list_targets(facility_id)],
                    "clock_offset": clock.offset, "writes_today": db.writes_today(facility_id)})


@api.post("/facilities/<int:facility_id>/targets")
def add_target(facility_id: int):
    fac, missing = facility_or_404(facility_id)
    if missing:
        return missing
    body = request.get_json(silent=True) or {}
    kind = body.get("kind")
    p = body.get("params") or {}
    if kind not in TARGET_KINDS:
        return err("unknown target kind")
    if not p.get("date"):
        return err("params.date is required")
    if kind.startswith("court") and not (p.get("scheduler_id") and p.get("start")):
        return err("court targets need scheduler_id and start")
    if kind == "court_reserve" and not p.get("reservation_type_id"):
        return err("pick a reservation type")
    if kind.startswith("event") and not p.get("event_id"):
        if not p.get("details_path"):
            return err("event targets need details_path or event_id")
        try:
            _resolve_event(fac, p)
        except (LoginError, CourtReserveError, SecretStoreError) as e:
            return err(str(e), 502)
    if kind == "event_register" and not p.get("opens_at") and not p.get("register_open"):
        opens = _event_opens_at(fac, p)
        if opens:
            p["opens_at"] = opens

    label = body.get("label") or f"{kind} {p.get('date')} {p.get('start') or ''}".strip()
    status = "watching" if kind in WATCHERS else "armed"
    fire_at = None
    if status == "armed" and p.get("opens_at"):
        fire_at = datetime.fromisoformat(p["opens_at"]).astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds")
    t = db.insert_target(facility_id, kind, label, fire_at, p, status=status, dry_run=True)
    when = f"fires {fire_at}" if fire_at else ("watching" if status == "watching" else "runs now")
    db.add_log("info", "target", f"#{t['id']} armed: {label} · {when} · dry run", facility_id)
    if status == "armed" and not fire_at:
        runner.run_now(int(t["id"]))
    return jsonify({"ok": True, "target": target_view(t)})


@api.post("/targets/<int:target_id>/run")
def run_target(target_id: int):
    t = db.get_target(target_id)
    if not t:
        return err("no such target", 404)
    fid = int(t["facility_id"])
    if t["kind"] in WATCHERS:
        if t["status"] != "watching":
            db.update_target(target_id, status="watching", result=None)
        db.add_log("info", "target", f"#{target_id} poll requested", fid)
        threading.Thread(target=polling.poll, args=(target_id,), daemon=True).start()
        return jsonify({"ok": True})
    if t["status"] in runner.FINAL:
        db.update_target(target_id, status="armed", result=None)
    db.add_log("info", "target", f"#{target_id} dry run requested", fid)
    runner.run_now(target_id)
    return jsonify({"ok": True})


@api.post("/targets/<int:target_id>/disarm")
def disarm_target(target_id: int):
    t = db.get_target(target_id)
    if not t:
        return err("no such target", 404)
    t = db.update_target(target_id, status="disarmed", result="disarmed by user")
    db.add_log("info", "target", f"#{target_id} disarmed", int(t["facility_id"]))
    return jsonify({"ok": True, "target": target_view(t)})


@api.delete("/targets/<int:target_id>")
def delete_target(target_id: int):
    if not db.get_target(target_id):
        return err("no such target", 404)
    db.delete_target(target_id)
    return jsonify({"ok": True})


@api.get("/targets/<int:target_id>/attempts")
def target_attempts(target_id: int):
    return jsonify({"ok": True, "attempts": db.list_attempts(target_id)})


@api.get("/facilities/<int:facility_id>/bookings")
def my_bookings(facility_id: int):
    fac, missing = facility_or_404(facility_id)
    if missing:
        return missing
    try:
        res = bookings_sync.sync(fac, force=request.args.get("refresh") == "1")
    except LoginError as e:
        registry.expire(facility_id)
        return err(str(e), 502)
    except (CourtReserveError, SecretStoreError) as e:
        return err(str(e), 502)
    sess = registry.get(facility_id)
    return jsonify({"ok": True, "bookings": [bookings_sync.view(b) for b in res["bookings"]],
                    "adopted": res["adopted"], "closed": res["closed"],
                    "fetched_at": sess.bookings_at.isoformat(timespec="seconds") if sess and sess.bookings_at else None})
