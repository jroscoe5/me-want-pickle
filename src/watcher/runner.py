"""Execute one target's timeline. Submits are dry-run only while ALLOW_SUBMIT is False."""

from __future__ import annotations

import random
import threading
import time
from datetime import date, datetime, timedelta, timezone

from .. import db
from ..courtreserve import CourtReserveError, LoginError
from ..courtreserve.client import APP_HOST
from ..sessions import registry
from . import clock
from .forms import FormSpec

MIN_GAP = 1.0
MAX_WRITES_PER_TARGET = 5
MAX_WRITES_PER_DAY = 40
SYNC_LEAD = timedelta(minutes=10)
PREFLIGHT_LEAD = timedelta(seconds=45)
HANDOFF_LEAD = timedelta(seconds=3)
POST_DELAY = (0.5, 1.0)
FINAL = {"done", "done_dry", "failed", "blocked", "disarmed", "expired"}
WATCH_AFTER = {"event_waitlist": "event_spot", "court_waitlist": "court_spot"}

_last_request: dict[int, float] = {}
_gap_lock = threading.Lock()
_preflight: dict[int, tuple[datetime, FormSpec, dict, str]] = {}


def pace(facility_id: int) -> None:
    with _gap_lock:
        wait = MIN_GAP - (time.monotonic() - _last_request.get(facility_id, 0))
        if wait > 0:
            time.sleep(wait)
        _last_request[facility_id] = time.monotonic()


def ampm(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def add_minutes(hhmm: str, minutes: int) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    t = h * 60 + m + minutes
    return f"{t // 60:02d}:{t % 60:02d}"


def attempt(t: dict, step: str, outcome: str, note: str | None = None, request: str | None = None,
            payload=None, is_write: bool = False) -> None:
    db.add_attempt(int(t["id"]), int(t["facility_id"]), step, outcome, request=request, note=note,
                   payload=payload, is_write=is_write)
    level = "error" if outcome in ("failed", "blocked") else ("warn" if outcome == "dry-run" else "info")
    db.add_log(level, "watcher", f"#{t['id']} {step}: {outcome}" + (f" · {note}" if note else ""), int(t["facility_id"]))


def _prepare_court_reserve(p: dict, client, fid: int) -> tuple[FormSpec, dict, str]:
    day, sid, start = date.fromisoformat(p["date"]), int(p["scheduler_id"]), p["start"]
    ct = int(p.get("court_type_id") or 9)
    duration = int(p.get("duration") or 60)
    pace(fid)
    spec = client.court_form(sid, day, start, add_minutes(start, 60), ct, p.get("court_type") or "Pickleball")
    if str(spec.flags.get("captcha", "")).lower() == "true":
        raise RuntimeError("blocked: CAPTCHA is enabled for this player")
    pace(fid)
    types = client.reservation_types(sid, day, start, add_minutes(start, 60), ct)
    chosen = next((x for x in types if x.id == int(p.get("reservation_type_id") or 0)), None)
    if not chosen:
        raise RuntimeError("failed: reservation type not offered for this slot: " + ", ".join(f"{x.name}({x.id})" for x in types))
    pace(fid)
    allowed = client.duration_options(sid, chosen.id, day, start, ct)
    if allowed and duration not in allowed:
        raise RuntimeError(f"failed: {chosen.name} offers {', '.join(str(x) for x in allowed)} min for this slot, not {duration}")
    partners = p.get("partners") or []
    if len(partners) + 1 < chosen.min_players:
        raise RuntimeError(f"failed: {chosen.name} needs at least {chosen.min_players} players")
    overrides = {"ReservationTypeId": str(chosen.id), "Duration": str(duration), "EndTime": ampm(add_minutes(start, duration)),
                 "MemberIds": ",".join(str(m["member_id"]) for m in partners if m.get("member_id"))}
    for i, m in enumerate(partners, start=1):
        first, _, last = (m.get("name") or "").partition(" ")
        overrides[f"SelectedMembers[{i}].OrgMemberId"] = str(m.get("org_member_id") or "")
        overrides[f"SelectedMembers[{i}].MemberId"] = str(m.get("member_id") or "")
        overrides[f"SelectedMembers[{i}].FirstName"] = first
        overrides[f"SelectedMembers[{i}].LastName"] = last
    return spec, overrides, client.bookings_url(sid)


def _prepare_court_waitlist(p: dict, client, fid: int) -> tuple[FormSpec, dict, str]:
    day, sid, start = date.fromisoformat(p["date"]), int(p["scheduler_id"]), p["start"]
    duration = int(p.get("duration") or 60)
    pace(fid)
    spec = client.court_waitlist_form(sid, day, start, add_minutes(start, 60))
    dtypes = [x for x in (spec.flags.get("duration_types") or "").split(",") if x]
    overrides = {"ReservationTypeId": str(p.get("reservation_type_id") or ""), "Duration": str(duration),
                 "StartTime": ampm(start), "EndTimeSpan": ampm(add_minutes(start, duration))}
    if dtypes:
        overrides["DurationType"] = dtypes[0]
    return spec, overrides, client.bookings_url(sid)


def _prepare_event_register(p: dict, client, fid: int) -> tuple[FormSpec, dict, str]:
    pace(fid)
    spec = client.event_signup_form(int(p["event_id"]), int(p["reservation_id"]), p["number"])
    return spec, {"SelectedNumberOfGuests": "0"}, f"{APP_HOST}/Online/Events/SignUpToEvent/{client.org_id}"


def _prepare_event_waitlist(p: dict, client, fid: int) -> tuple[FormSpec, dict, str]:
    pace(fid)
    spec = client.event_waitlist_form(int(p["event_id"]), int(p["reservation_id"]))
    return spec, {}, f"{APP_HOST}/Online/Events/SignUpToWaitingList/{client.org_id}"


PREPARERS = {
    "court_reserve": _prepare_court_reserve,
    "court_waitlist": _prepare_court_waitlist,
    "event_register": _prepare_event_register,
    "event_waitlist": _prepare_event_waitlist,
}


def prepare(t: dict, client, fac: dict) -> tuple[FormSpec, dict, str]:
    """Fetch the form for a target and compute the field overrides. Reads only."""
    fn = PREPARERS.get(t["kind"])
    if not fn:
        raise RuntimeError(f"failed: {t['kind']} targets are watched, not fired directly")
    return fn(t["params"], client, int(fac["id"]))


def _fail(t: dict, step: str, e: Exception) -> None:
    msg = str(e)
    status = "blocked" if msg.startswith("blocked") else "failed"
    attempt(t, step, status, note=msg)
    db.update_target(int(t["id"]), status=status, result=msg)


def presync(target_id: int) -> None:
    t = db.get_target(target_id)
    if not t or t["status"] != "armed":
        return
    value = clock.sync(int(t["facility_id"]), precise=True)
    p = dict(t["params"])
    p["synced_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.update_target(target_id, params=p)
    attempt(t, "clock", "ok", note=f"offset {value:+.3f}s")


def preflight(target_id: int) -> None:
    t = db.get_target(target_id)
    if not t or t["status"] not in ("armed", "preflight"):
        return
    fac = db.get_facility(int(t["facility_id"]))
    db.update_target(target_id, status="preflight")
    try:
        sess = registry.ensure(fac)
        attempt(t, "session", "ok")
        value = clock.sync(int(fac["id"]), precise=True)
        attempt(t, "clock", "ok", note=f"offset {value:+.3f}s")
        spec, overrides, referer = prepare(t, sess.client, fac)
        _preflight[target_id] = (datetime.now(timezone.utc), spec, overrides, referer)
        attempt(t, "preflight", "ok", note=f"{len(spec.fields)} fields · " + " · ".join(f"{k}={v}" for k, v in spec.flags.items() if v))
    except (LoginError, CourtReserveError, RuntimeError) as e:
        _fail(t, "preflight", e)


def fire(target_id: int) -> None:
    t = db.get_target(target_id)
    if not t or t["status"] in FINAL:
        return
    fac = db.get_facility(int(t["facility_id"]))
    fid = int(fac["id"])
    db.update_target(target_id, status="firing")
    try:
        if t["attempts"] >= MAX_WRITES_PER_TARGET:
            raise RuntimeError("failed: attempt budget for this target is spent")
        if db.writes_today(fid) >= MAX_WRITES_PER_DAY:
            raise RuntimeError("failed: daily write budget for this facility is spent")
        sess = registry.ensure(fac)
        cached = _preflight.pop(target_id, None)
        if cached and datetime.now(timezone.utc) - cached[0] < timedelta(minutes=3):
            _, spec, overrides, referer = cached
        else:
            spec, overrides, referer = prepare(t, sess.client, fac)
            attempt(t, "prepare", "ok", note=f"{len(spec.fields)} fields")
        if t.get("fire_at"):
            fire_local = clock.to_local(datetime.fromisoformat(t["fire_at"]))
            wait = (fire_local - datetime.now(timezone.utc)).total_seconds()
            if wait > 0:
                time.sleep(wait)
            late = (datetime.now(timezone.utc) - fire_local).total_seconds()
            attempt(t, "timing", "ok", note=f"reached T{late:+.3f}s (offset {clock.offset['seconds']:+.3f}s)")
        pause = random.uniform(*POST_DELAY)
        time.sleep(pause)
        attempt(t, "pause", "ok", note=f"{pause:.2f}s")
        pace(fid)
        res = sess.client.submit_form(spec, overrides, referer, dry_run=bool(t["dry_run"]))
        attempts = t["attempts"] + 1
        if res.get("dry_run"):
            attempt(t, "submit", "dry-run", note=f"would POST {res['action']}", request=f"POST {res['action']}",
                    payload=res["fields"])
            if t["kind"] in WATCH_AFTER:
                _start_watching(t, attempts, "waitlist join dry-run; now watching for a spot")
            else:
                db.update_target(target_id, status="done_dry", attempts=attempts, result="dry run complete; nothing was sent")
            return
        attempt(t, "submit", "sent", request=f"POST {spec.action}", payload=res, is_write=True)
        j = res.get("json") or {}
        if t["kind"] in WATCH_AFTER and j.get("isValid"):
            _start_watching(t, attempts, "joined the waitlist; now watching for a spot")
        elif j.get("isValid") and j.get("isRedirect"):
            db.update_target(target_id, status="done", attempts=attempts, result=f"payment required: {j.get('url')}")
        elif j.get("isValid"):
            db.update_target(target_id, status="done", attempts=attempts, result="confirmed")
        else:
            db.update_target(target_id, status="failed", attempts=attempts,
                             result=str(j.get("message") or res.get("text") or f"HTTP {res.get('status')}"))
    except (LoginError, CourtReserveError, RuntimeError) as e:
        _fail(t, "fire", e)


def _start_watching(t: dict, attempts: int, note: str) -> None:
    p = dict(t["params"], joined=True, next_poll_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    db.update_target(int(t["id"]), status="watching", kind=WATCH_AFTER[t["kind"]], attempts=attempts, params=p, result=note)
    attempt(t, "watch", "start", note=note)


def run_now(target_id: int) -> None:
    threading.Thread(target=fire, args=(target_id,), daemon=True, name=f"fire-{target_id}").start()
