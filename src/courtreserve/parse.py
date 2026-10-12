"""Regexes and pure parsing helpers for CourtReserve pages."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .models import Event

ORG_URL_RE = re.compile(r"/(?:Online|Portal)/[A-Za-z]+/[A-Za-z]+/(\d{3,8})(?:[/?#]|$)")



def parse_org_id(value: str) -> int:
    value = value.strip()
    if value.isdigit():
        return int(value)
    m = ORG_URL_RE.search(value)
    if m:
        return int(m.group(1))
    path = urlparse(value).path
    m = re.search(r"(\d{4,7})", path)
    if m:
        return int(m.group(1))
    raise ValueError("could not find a facility id; paste the portal URL or the numeric id")


TITLE_RE = re.compile(r"<title>\s*(.*?)\s*</title>", re.I | re.S)
AF_INPUT_RE = re.compile(r'__RequestVerificationToken[^>]*value="([^"]+)"')
BAD_CREDS_RE = re.compile(r"username or password is incorrect", re.I)
LOCKED_RE = re.compile(r"account (?:is )?locked|too many (?:failed )?attempts|verify your (?:email|account)", re.I)
MEMBER_ID_RE = re.compile(r"(?:memberId|authUserId)[=~]'?(\d{4,10})")
COST_TYPE_RE = re.compile(r"[Cc]ostTypeI[dD]['\"]?\s*[:=]\s*['\"]?(\d{3,10})")
TZ_RE = re.compile(r"TimeZone['\"]?\s*:\s*['\"]([A-Za-z_]+/[A-Za-z_]+)['\"]")
SID_RE = re.compile(r"/Online/Reservations/Bookings/\d+\?sId=(\d+)")
MIN_INTERVAL_RE = re.compile(r"ReservationMinInterval['\"]?\s*:\s*['\"]?(\d+)")
MS_DATE_RE = re.compile(r"/Date\((-?\d+)\)/")
APILIST_RE = re.compile(r"var url = '(https://events\.courtreserve\.com/Online/EventsApi/ApiList[^']+)'")
LOADEVENTS_RE = re.compile(r"https://events\.courtreserve\.com/Online/EventsApi/ApiLoadEvents[^'\"]+")
CATEGORY_RE = re.compile(r'id="category_checkbox_(\d+)".{0,500}?"label":"([^"]*)"', re.S)
CARD_SPLIT = 'data-testid="event-card"'
TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])", re.I)
SPOTS_RE = re.compile(r"(\d+)\s+of\s+(\d+)")
JWT_RE = re.compile(r"jwtToken = '([^']+)'")
REQDATA_ATTR_RE = re.compile(r'user-requestdata="([^"]+)"')
FIXURL_RE = re.compile(r"fixUrl\('([^']+)'\)")
SIGNUP_GET_RE = re.compile(r"(https://events\.courtreserve\.com/Online/EventsApi/EventApi_SignUpToEvent_Get[^'\"]+)")
SCHED_START_RE = re.compile(r'"startTime"\s*:\s*new Date\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)(?:,(\d+))?\)')
SCHED_END_RE = re.compile(r'"endTime"\s*:\s*new Date\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)(?:,(\d+))?\)')
SCHED_MIN_RE = re.compile(r'"min"\s*:\s*new Date\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)(?:,(\d+))?\)')
SCHED_MAX_RE = re.compile(r'"max"\s*:\s*new Date\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)(?:,(\d+))?\)')


def refusal(page: str) -> str | None:
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<style.*?</style>", " ", re.sub(r"<[^>]+>", " ", page), flags=re.S))).strip()
    m = re.search(r"(not eligible[^.]*\.?(?:[^.]*Restriction[^.]*\.?)?)", text, re.I)
    if m:
        return "refused: " + re.sub(r"\s+", " ", m.group(1)).strip()[:200]
    for kw in ("already registered", "already on the waitlist", "registration is closed", "no longer available"):
        if kw in text.lower():
            return "refused: " + kw
    return None


def first_int(rx: re.Pattern[str], text: str) -> int | None:
    m = rx.search(text)
    return int(m.group(1)) if m else None


def looks_logged_in(page: str, final_url: str) -> bool:
    if "/Account/LogIn" in final_url or "/Account/Login" in final_url:
        return False
    return "Account/LogOut" in page or '"IsAuthenticated": "True"' in page


def card_text(card: str, testid: str) -> str:
    m = re.search(r'data-testid="' + testid + r'[^"]*"[^>]*>(.*?)</(?:h\d|span|a|div)>', card, re.S)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()) if m else ""


def hhmm(h: str, m: str | None, ap: str) -> str:
    hour = int(h) % 12 + (12 if ap.lower() == "p" else 0)
    return f"{hour:02d}:{int(m or 0):02d}"


def parse_card(card: str) -> Event | None:
    name = card_text(card, "event-name")
    if not name:
        return None
    when = card_text(card, "date-time-section")
    times = TIME_RE.findall(when.split(",")[-1]) if when else []
    start = hhmm(*times[0]) if times else None
    end = hhmm(*times[1]) if len(times) > 1 else None
    spots = card_text(card, "slots-info")
    sm = SPOTS_RE.search(spots)
    reg = re.search(r'href="([^"]+)"[^>]*data-testid="register-btn"[^>]*>(.*?)</a>', card, re.S)
    url_m = re.search(r'data-url="([^"]+)"', card)
    path = html.unescape(reg.group(1) if reg else (url_m.group(1) if url_m else ""))
    rid = re.search(r"resId=(\d+)", path)
    return Event(
        category=card_text(card, "category-name"), name=name, when=when, start=start, end=end,
        cost=card_text(card, "cost"), spots=spots,
        spots_left=int(sm.group(1)) if sm else (0 if spots.lower() == "full" else None),
        spots_total=int(sm.group(2)) if sm else None,
        tags=[html.unescape(t.strip()) for t in re.findall(r'data-testid="tag-name[^"]*"\s*>\s*([^<]+?)\s*<', card)],
        action=re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", reg.group(2))).strip() if reg else "",
        details_path=path, reservation_id=int(rid.group(1)) if rid else None,
    )


def js_time(m: re.Match[str] | None) -> str | None:
    if not m:
        return None
    return f"{int(m.group(4)):02d}:{int(m.group(5)):02d}"


def js_date(m: re.Match[str] | None, tz: ZoneInfo | None) -> datetime | None:
    if not m:
        return None
    y, mo, d, h, mi, sec = (int(m.group(i)) for i in range(1, 7))
    return datetime(y, mo + 1, d, h, mi, sec, tzinfo=tz)


def ms_to_local(value, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    m = MS_DATE_RE.search(str(value))
    if not m:
        return None
    return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).astimezone(tz)
