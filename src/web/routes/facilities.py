from __future__ import annotations

from flask import jsonify, request

from ... import __version__, db, secrets
from ...courtreserve import CourtReserveClient, CourtReserveError, LoginError, parse_org_id
from ...secrets import SecretStoreError
from ...sessions import delete_cookies, registry, save_cookies
from .common import api, err, facility_or_404, facility_view


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
                    "facilities": [facility_view(f) for f in rows], "log": db.read_log(limit=60)})


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
        return err("facility, username, and password are all required")
    try:
        org_id = parse_org_id(facility_in)
    except ValueError as e:
        return err(str(e))
    if db.get_facility_by_org(org_id):
        return err(f"facility {org_id} is already set up; remove it first to change the account")

    db.add_log("info", "setup", f"verifying {username} at facility {org_id}")
    client = CourtReserveClient(org_id)
    try:
        client.login(username, password)
        info = client.facility_info()
    except LoginError as e:
        db.add_log("error", "setup", f"login rejected for facility {org_id}: {e}")
        return err(f"CourtReserve rejected the login: {e}", 401)
    except CourtReserveError as e:
        db.add_log("error", "setup", f"could not verify facility {org_id}: {e}")
        return err(str(e), 502)
    try:
        secrets.set_password(org_id, username, password)
    except SecretStoreError as e:
        db.add_log("error", "setup", str(e))
        return err(str(e), 500)

    fac = db.insert_facility(org_id=org_id, name=info.name, username=username, timezone=info.timezone,
                             cost_type_id=info.cost_type_id, member_id=info.member_id)
    registry.put(int(fac["id"]), client, status="live")
    save_cookies(client)
    db.add_log("info", "setup", f"added {info.name} (org {org_id}) as {username}", int(fac["id"]))
    return jsonify({"ok": True, "facility": facility_view(fac)})


@api.delete("/facilities/<int:facility_id>")
def remove_facility(facility_id: int):
    fac, missing = facility_or_404(facility_id)
    if missing:
        return missing
    registry.drop(facility_id)
    delete_cookies(int(fac["org_id"]))
    secrets.delete_password(int(fac["org_id"]), fac["username"])
    db.delete_facility(facility_id)
    db.add_log("info", "setup", f"removed {fac['name']} (org {fac['org_id']}) and its password")
    return jsonify({"ok": True})


@api.post("/facilities/<int:facility_id>/check")
def check_facility(facility_id: int):
    fac, missing = facility_or_404(facility_id)
    if missing:
        return missing
    try:
        registry.ensure(fac, verify=True)
    except (LoginError, CourtReserveError, SecretStoreError) as e:
        return jsonify({"ok": False, "error": str(e), "facility": facility_view(db.get_facility(facility_id) or fac)}), 502
    return jsonify({"ok": True, "facility": facility_view(db.get_facility(facility_id) or fac)})
