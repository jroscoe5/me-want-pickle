"""Server clock offset. CourtReserve's Date header is whole seconds; the precise
mode samples until it ticks and uses that edge."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from .. import db
from ..courtreserve.client import APP_HOST

offset = {"seconds": 0.0, "rtt_ms": None, "method": None, "checked_at": None}


def _sample() -> tuple[float, float, float] | None:
    t0 = time.time()
    try:
        r = requests.head(APP_HOST + "/", timeout=10, allow_redirects=False)
        server = parsedate_to_datetime(r.headers["Date"]).timestamp()
    except Exception:
        return None
    return t0, server, time.time() - t0


def sync(facility_id: int | None = None, precise: bool = False) -> float:
    samples = []
    if precise:
        last = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and len(samples) < 8:
            x = _sample()
            if x:
                samples.append(x)
                if last is not None and x[1] != last:
                    break
                last = x[1]
            time.sleep(0.18)
    else:
        for _ in range(3):
            x = _sample()
            if x:
                samples.append(x)
            time.sleep(0.3)
    if not samples:
        db.add_log("warn", "clock", "clock sync failed", facility_id)
        return offset["seconds"]
    if precise and len(samples) >= 2 and samples[-1][1] != samples[-2][1]:
        t0, server, rtt = samples[-1]
        value, method = server - (t0 + rtt / 2), "tick"
    else:
        t0, server, rtt = min(samples, key=lambda x: x[2])
        value, method = server + 0.5 - (t0 + rtt / 2), "min-rtt"
    offset.update(seconds=round(value, 3), rtt_ms=round(rtt * 1000), method=method,
                  checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    db.add_log("info", "clock", f"server clock offset {value:+.3f}s ({method}, rtt {rtt * 1000:.0f} ms)", facility_id)
    return value


def to_local(server_time: datetime) -> datetime:
    """Translate an instant on the server's clock to this machine's clock."""
    return server_time - timedelta(seconds=offset["seconds"])
