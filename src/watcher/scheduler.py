"""Background loop: preflight and fire armed targets on time, poll watchers."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from .. import db
from . import bookings, clock, polling, runner

TICK = 2.0
_started = threading.Event()
_inflight: set[int] = set()


def _due(t: dict, now: datetime) -> str | None:
    if t["status"] == "watching":
        nxt = t["params"].get("next_poll_at")
        return "poll" if not nxt or datetime.fromisoformat(nxt) <= now else None
    if t["status"] not in ("armed", "preflight") or not t.get("fire_at"):
        return None
    fire_local = clock.to_local(datetime.fromisoformat(t["fire_at"]))
    if now >= fire_local - runner.HANDOFF_LEAD:
        return "fire"
    if t["status"] == "armed" and now >= fire_local - runner.PREFLIGHT_LEAD:
        return "preflight"
    if t["status"] == "armed" and now >= fire_local - runner.SYNC_LEAD and not t["params"].get("synced_at"):
        return "presync"
    return None


def _run(action: str, target_id: int) -> None:
    try:
        if action == "fire":
            runner.fire(target_id)
        elif action == "preflight":
            runner.preflight(target_id)
        elif action == "presync":
            runner.presync(target_id)
        elif action == "poll":
            polling.poll(target_id)
    finally:
        _inflight.discard(target_id)


def _loop() -> None:
    db.add_log("info", "watcher", "scheduler running (dry-run only build)")
    last_sync = 0.0
    last_bookings = 0.0
    while True:
        try:
            now = datetime.now(timezone.utc)
            if time.monotonic() - last_sync > 600:
                clock.sync()
                last_sync = time.monotonic()
            if time.monotonic() - last_bookings > 600:
                last_bookings = time.monotonic()
                for fac in db.list_facilities():
                    try:
                        bookings.sync(fac)
                    except Exception as e:
                        db.add_log("warn", "sync", f"bookings sync failed: {e}", int(fac["id"]))
            for t in db.list_targets(statuses=("armed", "preflight", "watching")):
                tid = int(t["id"])
                if tid in _inflight:
                    continue
                action = _due(t, now)
                if action:
                    _inflight.add(tid)
                    threading.Thread(target=_run, args=(action, tid), daemon=True, name=f"{action}-{tid}").start()
        except Exception as e:  # keep the loop alive
            db.add_log("error", "watcher", f"scheduler error: {e}")
        time.sleep(TICK)


def start_scheduler() -> None:
    if _started.is_set():
        return
    _started.set()
    threading.Thread(target=_loop, daemon=True, name="watcher").start()
