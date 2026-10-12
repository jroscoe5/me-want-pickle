from __future__ import annotations

from datetime import date

from flask import Blueprint, jsonify, request

from ... import db
from ...sessions import registry

api = Blueprint("api", __name__)

FACILITY_FIELDS = ("id", "org_id", "name", "username", "timezone", "cost_type_id", "member_id",
                   "created_at", "verified_at", "horizon_min", "horizon_max", "horizon_at")


def facility_view(f: dict) -> dict:
    out = {k: f[k] for k in FACILITY_FIELDS}
    out["session"] = registry.status_of(int(f["id"]))
    return out


def err(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def day_arg() -> date:
    return date.fromisoformat(request.args.get("date") or date.today().isoformat())


def facility_or_404(facility_id: int):
    fac = db.get_facility(facility_id)
    return fac, (None if fac else err("no such facility", 404))
