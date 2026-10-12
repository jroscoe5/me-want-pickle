from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import jsonify, request

from ... import db
from ...courtreserve import CourtReserveError, Event, LoginError
from ...secrets import SecretStoreError
from ...sessions import registry
from .common import api, day_arg, err, facility_or_404

EVENTS_CTX_TTL = timedelta(minutes=30)


def events_ctx(sess, force: bool = False):
    ctx = sess.events
    if force or ctx is None or datetime.now(ZoneInfo("UTC")) - ctx.fetched_at > EVENTS_CTX_TTL:
        ctx = sess.client.events_context()
        sess.events = ctx
    return ctx


def event_view(e: Event) -> dict:
    url = f"https://app.courtreserve.com{e.details_path}" if e.details_path.startswith("/") else e.details_path
    return {"category": e.category, "name": e.name, "when": e.when, "start": e.start, "end": e.end, "cost": e.cost,
            "spots": e.spots, "spots_left": e.spots_left, "spots_total": e.spots_total, "tags": e.tags,
            "action": e.action, "details_url": url, "reservation_id": e.reservation_id}


@api.get("/facilities/<int:facility_id>/events")
def events(facility_id: int):
    fac, missing = facility_or_404(facility_id)
    if missing:
        return missing
    try:
        day = day_arg()
    except ValueError:
        return err("date must be YYYY-MM-DD")
    want = (request.args.get("category") or "open play").lower()
    try:
        sess = registry.ensure(fac)
        for attempt in range(2):
            ctx = events_ctx(sess, force=attempt == 1)
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
        return err(str(e), 502)
    except (CourtReserveError, SecretStoreError) as e:
        db.add_log("error", "events", f"events {day}: {e}", facility_id)
        return err(str(e), 502)
    found.sort(key=lambda e: (e.start or "", e.name))
    return jsonify({"ok": True, "date": day.isoformat(), "categories": [c.name for c in cats],
                    "all_categories": [{"id": c.id, "name": c.name} for c in ctx.categories],
                    "events": [event_view(e) for e in found]})
