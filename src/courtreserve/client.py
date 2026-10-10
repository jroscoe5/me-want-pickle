"""CourtReserve member portal client. Endpoint notes: docs/GAME_PLAN.md."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

APP_HOST = "https://app.courtreserve.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


class CourtReserveError(Exception):
    pass


class LoginError(CourtReserveError):
    pass


@dataclass
class FacilityInfo:
    org_id: int
    name: str
    timezone: str | None = None
    cost_type_id: int | None = None
    member_id: int | None = None


@dataclass
class Scheduler:
    id: int
    name: str
    cost_type_id: int | None = None
    timezone: str | None = None
    min_interval: int = 60
    book_min: datetime | None = None
    book_max: datetime | None = None


@dataclass
class Slot:
    start: datetime
    end: datetime
    court_type: str
    court_type_id: int | None
    available: int
    court_ids: list[int] = field(default_factory=list)
    is_past: bool = False
    is_closed: bool = False
    show_waitlist: bool = False
    waitlist_count: int | None = None


@dataclass
class EventCategory:
    id: int
    name: str


@dataclass
class EventsContext:
    load_url: str
    categories: list[EventCategory]
    fetched_at: datetime


@dataclass
class Event:
    category: str
    name: str
    when: str
    start: str | None
    end: str | None
    cost: str
    spots: str
    spots_left: int | None
    spots_total: int | None
    tags: list[str]
    action: str
    details_path: str
    reservation_id: int | None


_ORG_URL_RE = re.compile(r"/(?:Online|Portal)/[A-Za-z]+/[A-Za-z]+/(\d{3,8})(?:[/?#]|$)")


def parse_org_id(value: str) -> int:
    value = value.strip()
    if value.isdigit():
        return int(value)
    m = _ORG_URL_RE.search(value)
    if m:
        return int(m.group(1))
    path = urlparse(value).path
    m = re.search(r"(\d{4,7})", path)
    if m:
        return int(m.group(1))
    raise ValueError("could not find a facility id; paste the portal URL or the numeric id")


_TITLE_RE = re.compile(r"<title>\s*(.*?)\s*</title>", re.I | re.S)
_AF_INPUT_RE = re.compile(r'__RequestVerificationToken[^>]*value="([^"]+)"')
_BAD_CREDS_RE = re.compile(r"username or password is incorrect", re.I)
_LOCKED_RE = re.compile(r"account (?:is )?locked|too many (?:failed )?attempts|verify your (?:email|account)", re.I)
_MEMBER_ID_RE = re.compile(r"(?:memberId|authUserId)[=~]'?(\d{4,10})")
_COST_TYPE_RE = re.compile(r"[Cc]ostTypeI[dD]['\"]?\s*[:=]\s*['\"]?(\d{3,10})")
_TZ_RE = re.compile(r"TimeZone['\"]?\s*:\s*['\"]([A-Za-z_]+/[A-Za-z_]+)['\"]")
_SID_RE = re.compile(r"/Online/Reservations/Bookings/\d+\?sId=(\d+)")
_MIN_INTERVAL_RE = re.compile(r"ReservationMinInterval['\"]?\s*:\s*['\"]?(\d+)")
_MS_DATE_RE = re.compile(r"/Date\((-?\d+)\)/")
_APILIST_RE = re.compile(r"var url = '(https://events\.courtreserve\.com/Online/EventsApi/ApiList[^']+)'")
_LOADEVENTS_RE = re.compile(r"https://events\.courtreserve\.com/Online/EventsApi/ApiLoadEvents[^'\"]+")
_CATEGORY_RE = re.compile(r'id="category_checkbox_(\d+)".{0,500}?"label":"([^"]*)"', re.S)
_CARD_SPLIT = 'data-testid="event-card"'
_TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])", re.I)
_SPOTS_RE = re.compile(r"(\d+)\s+of\s+(\d+)")
_SCHED_MIN_RE = re.compile(r'"min"\s*:\s*new Date\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)(?:,(\d+))?\)')
_SCHED_MAX_RE = re.compile(r'"max"\s*:\s*new Date\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)(?:,(\d+))?\)')


class CourtReserveClient:
    def __init__(self, org_id: int, timeout: float = 15.0):
        self.org_id = int(org_id)
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    @property
    def login_url(self) -> str:
        return f"{APP_HOST}/Online/Account/LogIn/{self.org_id}"

    @property
    def portal_url(self) -> str:
        return f"{APP_HOST}/Online/Portal/Index/{self.org_id}"

    def bookings_url(self, scheduler_id: int) -> str:
        return f"{APP_HOST}/Online/Reservations/Bookings/{self.org_id}?sId={scheduler_id}"

    def login(self, username: str, password: str) -> dict:
        try:
            r0 = self.s.get(self.login_url, timeout=self.timeout)
            m = _AF_INPUT_RE.search(r0.text)
            token = m.group(1) if m else None
            headers = {
                "Accept": "application/json, text/plain, */*",
                "Origin": APP_HOST,
                "Referer": r0.url,
                "X-Requested-With": "XMLHttpRequest",
            }
            if token:
                headers["RequestVerificationToken"] = token
            r = self.s.post(
                f"{APP_HOST}/Account/LogInReact",
                json={
                    "Username": username,
                    "Password": password,
                    "SpGuideId": "",
                    "IsApiCall": True,
                    "RedirectToMyClubs": False,
                    "LoginAsAMember": True,
                    "OrganizationId": self.org_id,
                },
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise LoginError(f"network error talking to CourtReserve: {e}") from e

        if r.status_code >= 500:
            raise LoginError(f"CourtReserve returned HTTP {r.status_code} on login")

        ctype = r.headers.get("content-type", "")
        if "json" in ctype:
            try:
                data = r.json()
            except ValueError:
                data = None
            if isinstance(data, dict):
                valid = data.get("isValid", data.get("IsValid"))
                if valid:
                    return {"via": "json", "redirect": data.get("redirectUrl")}
                msg = data.get("message") or data.get("Message") or "username or password rejected"
                raise LoginError(str(msg))

        text = r.text
        if _BAD_CREDS_RE.search(text):
            raise LoginError("the username or password is incorrect")
        if _LOCKED_RE.search(text):
            raise LoginError("CourtReserve says the account is locked or needs verification; sign in on the website first")

        if self.is_logged_in():
            return {"via": "portal-check", "redirect": None}
        raise LoginError(
            f"login did not establish a session (HTTP {r.status_code}, {ctype.split(';')[0] or 'no content type'})"
        )

    def is_logged_in(self) -> bool:
        try:
            r = self.s.get(self.portal_url, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException:
            return False
        return _looks_logged_in(r.text, r.url)

    def facility_info(self) -> FacilityInfo:
        try:
            r = self.s.get(self.portal_url, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException as e:
            raise CourtReserveError(f"network error fetching portal: {e}") from e
        text = r.text
        if not _looks_logged_in(text, r.url):
            raise LoginError("session is not signed in to this facility")

        name = f"facility {self.org_id}"
        m = _TITLE_RE.search(text)
        if m:
            title = html.unescape(m.group(1))
            name = title.split("| powered by")[0].strip() or name

        def first_int(rx: re.Pattern[str]) -> int | None:
            mm = rx.search(text)
            return int(mm.group(1)) if mm else None

        tz = None
        mt = _TZ_RE.search(text)
        if mt:
            tz = mt.group(1)

        return FacilityInfo(
            org_id=self.org_id,
            name=name,
            timezone=tz,
            cost_type_id=first_int(_COST_TYPE_RE),
            member_id=first_int(_MEMBER_ID_RE),
        )


    def schedulers(self) -> list[Scheduler]:
        try:
            portal = self.s.get(self.portal_url, timeout=self.timeout).text
        except requests.RequestException as e:
            raise CourtReserveError(f"network error fetching portal: {e}") from e
        seen: list[int] = []
        for sid in _SID_RE.findall(portal):
            if int(sid) not in seen:
                seen.append(int(sid))
        out = []
        for sid in seen:
            try:
                page = self.s.get(self.bookings_url(sid), timeout=self.timeout).text
            except requests.RequestException as e:
                raise CourtReserveError(f"network error fetching scheduler {sid}: {e}") from e
            name = f"scheduler {sid}"
            m = _TITLE_RE.search(page)
            if m:
                name = html.unescape(m.group(1)).split("| powered by")[0].strip() or name
            mc = _COST_TYPE_RE.search(page)
            mt = _TZ_RE.search(page)
            mi = _MIN_INTERVAL_RE.search(page)
            tz = ZoneInfo(mt.group(1)) if mt else None
            out.append(Scheduler(
                id=sid, name=name,
                cost_type_id=int(mc.group(1)) if mc else None,
                timezone=mt.group(1) if mt else None,
                min_interval=int(mi.group(1)) if mi else 60,
                book_min=_js_date(_SCHED_MIN_RE.search(page), tz),
                book_max=_js_date(_SCHED_MAX_RE.search(page), tz),
            ))
        return out

    def events_context(self) -> EventsContext:
        referer = f"{APP_HOST}/Online/Events/List/{self.org_id}"
        try:
            page = self.s.get(referer, timeout=self.timeout).text
            m = _APILIST_RE.search(page)
            if not m:
                if not _looks_logged_in(page, referer):
                    raise LoginError("session expired")
                raise CourtReserveError("events list page has no ApiList url")
            partial = self.s.get(html.unescape(m.group(1)), params={"onlineListCookie": ""},
                                 headers={"X-Requested-With": "XMLHttpRequest", "Referer": referer, "Origin": APP_HOST},
                                 timeout=self.timeout).text
        except requests.RequestException as e:
            raise CourtReserveError(f"network error loading events: {e}") from e
        m2 = _LOADEVENTS_RE.search(partial)
        if not m2:
            raise CourtReserveError("events partial has no ApiLoadEvents url")
        cats = [EventCategory(int(i), html.unescape(json.loads(f'"{n}"')).strip()) for i, n in _CATEGORY_RE.findall(partial)]
        return EventsContext(load_url=html.unescape(m2.group(0)), categories=cats, fetched_at=datetime.now(timezone.utc))

    def list_events(self, ctx: EventsContext, day: date, category_ids: list[int], cost_type_id: int | None) -> list[Event]:
        mdy = day.strftime("%m/%d/%Y")
        body = {
            "MinPrice": "", "MaxPrice": "", "SessionIdsString": "", "InstructorIdsString": "",
            "EventTypeIdsString": ",".join(str(i) for i in category_ids), "TimeOfDayString": "", "DayOfWeeksString": "",
            "DatesString": "5", "FilterText": "", "CustomDate_Start": mdy, "CustomDate_End": mdy,
            "FilterTimeOfADayStart": "", "FilterTimeOfADayEnd": "", "EventRegistrationTypeId": "", "EventSortBy": "2",
            "EmbedCodeId": "", "Filter": "", "preventCookieSave": "true", "FilterPublicKey": "",
            "HideIneligibleAndFullEvents": "false", "CostTypeId": str(cost_type_id or ""), "SkipRows": "0", "EventTagIdsString": "",
        }
        try:
            r = self.s.post(ctx.load_url, data=body,
                            headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{APP_HOST}/Online/Events/List/{self.org_id}", "Origin": APP_HOST},
                            timeout=self.timeout)
            j = r.json()
        except requests.RequestException as e:
            raise CourtReserveError(f"network error loading events: {e}") from e
        except ValueError:
            raise CourtReserveError(f"unexpected events response (HTTP {r.status_code})")
        if not j.get("isValid"):
            raise CourtReserveError("events query rejected")
        return [ev for ev in (_parse_card(c) for c in (j.get("data") or "").split(_CARD_SPLIT)[1:]) if ev]

    def booking_horizon(self, scheduler_id: int) -> tuple[datetime | None, datetime | None]:
        try:
            page = self.s.get(self.bookings_url(scheduler_id), timeout=self.timeout).text
        except requests.RequestException as e:
            raise CourtReserveError(f"network error fetching scheduler {scheduler_id}: {e}") from e
        if not _looks_logged_in(page, self.bookings_url(scheduler_id)):
            raise LoginError("session expired")
        mt = _TZ_RE.search(page)
        tz = ZoneInfo(mt.group(1)) if mt else None
        return _js_date(_SCHED_MIN_RE.search(page), tz), _js_date(_SCHED_MAX_RE.search(page), tz)

    def read_slots(self, sched: Scheduler, day: date) -> list[Slot]:
        tz = ZoneInfo(sched.timezone or "UTC")
        local_midnight = datetime(day.year, day.month, day.day, tzinfo=tz)
        utc = local_midnight.astimezone(timezone.utc)
        body = {
            "startDate": utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "orgId": str(self.org_id),
            "TimeZone": sched.timezone or "UTC",
            "Date": utc.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "KendoDate": {"Year": day.year, "Month": day.month, "Day": day.day},
            "UiCulture": "en-US",
            "CostTypeId": str(sched.cost_type_id or ""),
            "CustomSchedulerId": str(sched.id),
            "ReservationMinInterval": str(sched.min_interval),
        }
        try:
            r = self.s.post(
                f"{APP_HOST}/Online/Reservations/ReadConsolidated/{self.org_id}",
                data={"jsonData": json.dumps(body)},
                headers={
                    "Accept": "*/*",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": self.bookings_url(sched.id),
                    "Origin": APP_HOST,
                },
                timeout=self.timeout,
            )
            data = r.json()
        except requests.RequestException as e:
            raise CourtReserveError(f"network error reading slots: {e}") from e
        except ValueError:
            if "/Account/LogIn" in r.url or "Account/LogIn" in r.text[:2000]:
                raise LoginError("session expired")
            raise CourtReserveError(f"unexpected slots response (HTTP {r.status_code})")
        rows = data.get("Data") or []
        out = []
        for row in rows:
            start = _ms_to_local(row.get("Start"), tz)
            end = _ms_to_local(row.get("End"), tz)
            if start is None or end is None:
                continue
            out.append(Slot(
                start=start, end=end,
                court_type=row.get("CourtType") or "",
                court_type_id=row.get("CourtTypeId"),
                available=int(row.get("AvailableCourts") or 0),
                court_ids=[int(x) for x in (row.get("AvailableCourtIds") or [])],
                is_past=bool(row.get("IsInPast")),
                is_closed=bool(row.get("IsClosed")),
                show_waitlist=bool(row.get("ShowWaitList")),
                waitlist_count=row.get("WaitListCount"),
            ))
        out.sort(key=lambda x: x.start)
        return out


def _looks_logged_in(page: str, final_url: str) -> bool:
    if "/Account/LogIn" in final_url or "/Account/Login" in final_url:
        return False
    return "Account/LogOut" in page or '"IsAuthenticated": "True"' in page


def _text(card: str, testid: str) -> str:
    m = re.search(r'data-testid="' + testid + r'[^"]*"[^>]*>(.*?)</(?:h\d|span|a|div)>', card, re.S)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()) if m else ""


def _hhmm(h: str, m: str | None, ap: str) -> str:
    hour = int(h) % 12 + (12 if ap.lower() == "p" else 0)
    return f"{hour:02d}:{int(m or 0):02d}"


def _parse_card(card: str) -> Event | None:
    name = _text(card, "event-name")
    if not name:
        return None
    when = _text(card, "date-time-section")
    times = _TIME_RE.findall(when.split(",")[-1]) if when else []
    start = _hhmm(*times[0]) if times else None
    end = _hhmm(*times[1]) if len(times) > 1 else None
    spots = _text(card, "slots-info")
    sm = _SPOTS_RE.search(spots)
    reg = re.search(r'href="([^"]+)"[^>]*data-testid="register-btn"[^>]*>(.*?)</a>', card, re.S)
    url_m = re.search(r'data-url="([^"]+)"', card)
    path = html.unescape(reg.group(1) if reg else (url_m.group(1) if url_m else ""))
    rid = re.search(r"resId=(\d+)", path)
    return Event(
        category=_text(card, "category-name"), name=name, when=when, start=start, end=end,
        cost=_text(card, "cost"), spots=spots,
        spots_left=int(sm.group(1)) if sm else (0 if spots.lower() == "full" else None),
        spots_total=int(sm.group(2)) if sm else None,
        tags=[html.unescape(t.strip()) for t in re.findall(r'data-testid="tag-name[^"]*"\s*>\s*([^<]+?)\s*<', card)],
        action=re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", reg.group(2))).strip() if reg else "",
        details_path=path, reservation_id=int(rid.group(1)) if rid else None,
    )


def _js_date(m: re.Match[str] | None, tz: ZoneInfo | None) -> datetime | None:
    if not m:
        return None
    y, mo, d, h, mi, sec = (int(m.group(i)) for i in range(1, 7))
    return datetime(y, mo + 1, d, h, mi, sec, tzinfo=tz)


def _ms_to_local(value, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    m = _MS_DATE_RE.search(str(value))
    if not m:
        return None
    return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).astimezone(tz)
