"""CourtReserve member portal client. Endpoint notes: docs/GAME_PLAN.md."""

from __future__ import annotations

import base64
import html
import json
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from ..watcher.forms import FormSpec, parse_form, redacted
from .models import (
    Booking, BookingsToken, CourtReserveError, Event, EventCategory, EventsContext, FacilityInfo, LoginError, Member,
    ReservationType, Scheduler, Slot,
)
from .parse import (
    AF_INPUT_RE, APILIST_RE, BAD_CREDS_RE, CARD_SPLIT, CATEGORY_RE, COST_TYPE_RE, FIXURL_RE, JWT_RE, LOADEVENTS_RE,
    LOCKED_RE, MEMBER_ID_RE, MIN_INTERVAL_RE, REQDATA_ATTR_RE, SCHED_END_RE, SCHED_MAX_RE, SCHED_MIN_RE, SCHED_START_RE,
    SID_RE, SIGNUP_GET_RE, TITLE_RE, TZ_RE, first_int, js_date, js_time, looks_logged_in, ms_to_local, parse_card, refusal,
)

APP_HOST = "https://app.courtreserve.com"
API4_HOST = "https://api4.courtreserve.com"
BACKEND_HOST = "https://backend.courtreserve.com"
ALLOW_SUBMIT = False
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


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

    def login(self, username: str, password: str) -> None:
        try:
            r0 = self.s.get(self.login_url, timeout=self.timeout)
            m = AF_INPUT_RE.search(r0.text)
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
                    return
                msg = data.get("message") or data.get("Message") or "username or password rejected"
                raise LoginError(str(msg))

        text = r.text
        if BAD_CREDS_RE.search(text):
            raise LoginError("the username or password is incorrect")
        if LOCKED_RE.search(text):
            raise LoginError("CourtReserve says the account is locked or needs verification; sign in on the website first")

        if self.is_logged_in():
            return
        raise LoginError(
            f"login did not establish a session (HTTP {r.status_code}, {ctype.split(';')[0] or 'no content type'})"
        )

    def is_logged_in(self) -> bool:
        try:
            r = self.s.get(self.portal_url, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException:
            return False
        return looks_logged_in(r.text, r.url)

    def facility_info(self) -> FacilityInfo:
        try:
            r = self.s.get(self.portal_url, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException as e:
            raise CourtReserveError(f"network error fetching portal: {e}") from e
        text = r.text
        if not looks_logged_in(text, r.url):
            raise LoginError("session is not signed in to this facility")

        name = f"facility {self.org_id}"
        m = TITLE_RE.search(text)
        if m:
            title = html.unescape(m.group(1))
            name = title.split("| powered by")[0].strip() or name

        mt = TZ_RE.search(text)
        return FacilityInfo(
            org_id=self.org_id, name=name, timezone=mt.group(1) if mt else None,
            cost_type_id=first_int(COST_TYPE_RE, text), member_id=first_int(MEMBER_ID_RE, text),
        )

    def schedulers(self) -> list[Scheduler]:
        try:
            portal = self.s.get(self.portal_url, timeout=self.timeout).text
        except requests.RequestException as e:
            raise CourtReserveError(f"network error fetching portal: {e}") from e
        seen: list[int] = []
        for sid in SID_RE.findall(portal):
            if int(sid) not in seen:
                seen.append(int(sid))
        return [self._scheduler_page(sid) for sid in seen]

    def _scheduler_page(self, sid: int) -> Scheduler:
        try:
            page = self.s.get(self.bookings_url(sid), timeout=self.timeout).text
        except requests.RequestException as e:
            raise CourtReserveError(f"network error fetching scheduler {sid}: {e}") from e
        if not looks_logged_in(page, self.bookings_url(sid)):
            raise LoginError("session expired")
        name = f"scheduler {sid}"
        m = TITLE_RE.search(page)
        if m:
            name = html.unescape(m.group(1)).split("| powered by")[0].strip() or name
        mt = TZ_RE.search(page)
        tz = ZoneInfo(mt.group(1)) if mt else None
        return Scheduler(
            id=sid, name=name, cost_type_id=first_int(COST_TYPE_RE, page), timezone=mt.group(1) if mt else None,
            min_interval=first_int(MIN_INTERVAL_RE, page) or 60,
            book_min=js_date(SCHED_MIN_RE.search(page), tz), book_max=js_date(SCHED_MAX_RE.search(page), tz),
            open_time=js_time(SCHED_START_RE.search(page)), close_time=js_time(SCHED_END_RE.search(page)),
        )

    def events_context(self) -> EventsContext:
        referer = f"{APP_HOST}/Online/Events/List/{self.org_id}"
        try:
            page = self.s.get(referer, timeout=self.timeout).text
            m = APILIST_RE.search(page)
            if not m:
                if not looks_logged_in(page, referer):
                    raise LoginError("session expired")
                raise CourtReserveError("events list page has no ApiList url")
            partial = self.s.get(html.unescape(m.group(1)), params={"onlineListCookie": ""},
                                 headers={"X-Requested-With": "XMLHttpRequest", "Referer": referer, "Origin": APP_HOST},
                                 timeout=self.timeout).text
        except requests.RequestException as e:
            raise CourtReserveError(f"network error loading events: {e}") from e
        m2 = LOADEVENTS_RE.search(partial)
        if not m2:
            raise CourtReserveError("events partial has no ApiLoadEvents url")
        cats = [EventCategory(int(i), html.unescape(json.loads(f'"{n}"')).strip()) for i, n in CATEGORY_RE.findall(partial)]
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
        return [ev for ev in (parse_card(c) for c in (j.get("data") or "").split(CARD_SPLIT)[1:]) if ev]

    def _xhr(self, referer: str) -> dict[str, str]:
        return {"X-Requested-With": "XMLHttpRequest", "Origin": APP_HOST, "Referer": referer}

    @staticmethod
    def _mdy(day: date) -> str:
        return f"{day.month}/{day.day}/{day.year}"

    @staticmethod
    def _ampm(hhmm: str) -> str:
        h, m = (int(x) for x in hhmm.split(":"))
        return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"

    def court_form(self, scheduler_id: int, day: date, start: str, end: str, court_type_id: int, court_type: str) -> FormSpec:
        referer = self.bookings_url(scheduler_id)
        params = {"start": f"{self._mdy(day)} {self._ampm(start)}", "end": f"{self._mdy(day)} {self._ampm(end)}",
                  "customSchedulerId": str(scheduler_id), "courtTypeId": str(court_type_id), "courtType": court_type}
        try:
            shell = self.s.get(f"{APP_HOST}/Online/Reservations/CreateReservation/{self.org_id}", params=params,
                               headers=self._xhr(referer), timeout=self.timeout).text
            m = FIXURL_RE.search(shell)
            if not m:
                raise CourtReserveError("reservation modal shell has no form url")
            r = self.s.get(html.unescape(m.group(1)), headers=self._xhr(referer), timeout=self.timeout)
        except requests.RequestException as e:
            raise CourtReserveError(f"network error loading reservation form: {e}") from e
        if "json" in r.headers.get("content-type", ""):
            j = r.json()
            raise CourtReserveError(j.get("errorMessage") or "reservation form refused")
        spec = parse_form(r.text, form_id="createReservation-Form")
        if not spec:
            raise CourtReserveError("reservation form not found in modal")
        spec.flags["captcha"] = spec.get("IsCaptchaEnabledForPlayer")
        spec.flags["payment_required"] = spec.get("RequirePaymentWhenBookingCourtsOnline")
        return spec

    def reservation_types(self, scheduler_id: int, day: date, start: str, end: str, court_type_id: int) -> list[ReservationType]:
        try:
            r = self.s.post(f"{APP_HOST}/Online/AjaxReservation/GetAvailableReservationTypes/{self.org_id}",
                            data={"customSchedulerId": str(scheduler_id), "userId": "", "startTime": f"{start}:00",
                                  "date": f"{self._mdy(day)} 12:00:00 AM", "courtId": "", "courtType": str(court_type_id),
                                  "endTime": self._ampm(end), "isDynamicSlot": "False", "instructorId": ""},
                            headers=self._xhr(self.bookings_url(scheduler_id)), timeout=self.timeout)
            rows = r.json()
        except (requests.RequestException, ValueError) as e:
            raise CourtReserveError(f"could not load reservation types: {e}") from e
        return [ReservationType(id=int(x["Id"]), name=str(x.get("Name", "")).strip(), max_time=int(x.get("MaxTime") or 0),
                                min_time=int(x.get("MinTime") or 0), min_players=int(x.get("MinimumNumberOfPlayers") or 0),
                                max_players=int(x.get("MaximumNumberOfPlayers") or 0)) for x in rows if x.get("IsActive", True)]

    def duration_options(self, scheduler_id: int, type_id: int, day: date, start: str, court_type_id: int) -> list[int]:
        try:
            r = self.s.get(f"{API4_HOST}/api/v1/portalreservationsapi/GetDurationDropdown",
                           params={"id": self.org_id, "reservationTypeId": type_id, "startTime": f"{start}:00",
                                   "selectedDate": self._mdy(day), "uiCulture": "en-US", "useMinTimeAsDefault": "False",
                                   "courtId": "", "courtType": str(court_type_id), "endTime": "", "isDynamicSlot": "False",
                                   "instructorId": "", "customSchedulerId": str(scheduler_id), "selectedDuration": ""},
                           headers=self._xhr(self.bookings_url(scheduler_id)), timeout=self.timeout)
            rows = r.json()
        except (requests.RequestException, ValueError) as e:
            raise CourtReserveError(f"could not load durations: {e}") from e
        return [int(x["Value"]) for x in rows if not x.get("Disabled") and str(x.get("Value", "")).isdigit()]

    def search_members(self, query: str, cost_type_id: int | None, org_member_id: int | None, scheduler_id: int | None) -> list[Member]:
        try:
            r = self.s.get(f"{API4_HOST}/api/v1/portalreservationsapi/Api_Reservation_GetMembersToPlayWith",
                           params={"id": self.org_id, "costTypeId": cost_type_id or "", "filterValue": query,
                                   "organizationMemberIdsString": str(org_member_id or ""), "userId": "",
                                   "customSchedulerId": scheduler_id or "", "isOpenReservation": "false"},
                           headers=self._xhr(self.bookings_url(scheduler_id or 0)), timeout=self.timeout)
            rows = r.json()
        except (requests.RequestException, ValueError) as e:
            raise CourtReserveError(f"member search failed: {e}") from e
        if isinstance(rows, dict):
            rows = rows.get("Data") or rows.get("data") or []
        out = []
        for x in rows:
            oid = x.get("OrgMemberId") or x.get("OrganizationMemberId") or x.get("Id")
            if oid is None:
                continue
            out.append(Member(org_member_id=int(oid), member_id=int(x["MemberId"]) if x.get("MemberId") else None,
                              name=str(x.get("DisplayName") or x.get("FullName") or x.get("Name") or "").strip()))
        return out

    def court_waitlist_form(self, scheduler_id: int, day: date, start: str, end: str) -> FormSpec:
        referer = self.bookings_url(scheduler_id)
        try:
            r = self.s.get(f"{APP_HOST}/Online/ReservationQueuePortal/JoinQueue/{self.org_id}",
                           params={"csId": str(scheduler_id), "start": f"{self._mdy(day)} {self._ampm(start)}",
                                   "end": f"{self._mdy(day)} {self._ampm(end)}"},
                           headers=self._xhr(referer), timeout=self.timeout)
        except requests.RequestException as e:
            raise CourtReserveError(f"network error loading waitlist form: {e}") from e
        spec = parse_form(r.text, action_contains="JoinQueue")
        if not spec:
            raise CourtReserveError("waitlist form not found")
        if spec.action.startswith("/"):
            spec.action = APP_HOST + spec.action
        spec.flags["duration_types"] = ",".join(re.findall(r'name="DurationType"[^>]*value="([^"]*)"', r.text))
        return spec

    def event_signup_form(self, event_id: int, reservation_id: int, number: str) -> FormSpec:
        page_url = (f"{APP_HOST}/Online/Events/SignUpToEvent/{self.org_id}?eventId={event_id}"
                    f"&reservationId={reservation_id}&reservationNumber={number}")
        try:
            page = self.s.get(page_url, timeout=self.timeout).text
            m = SIGNUP_GET_RE.search(page)
            if not m:
                if not looks_logged_in(page, page_url):
                    raise LoginError("session expired")
                raise CourtReserveError("sign-up page has no form loader (registration may not be open)")
            r = self.s.get(html.unescape(m.group(1)), headers=self._xhr(page_url), timeout=self.timeout)
        except requests.RequestException as e:
            raise CourtReserveError(f"network error loading sign-up form: {e}") from e
        body = r.text
        if "json" in r.headers.get("content-type", ""):
            j = r.json()
            if isinstance(j, dict) and j.get("returnUrl"):
                raise CourtReserveError(j.get("errorMessage") or "sign-up refused")
            body = j.get("data") or j.get("html") or body if isinstance(j, dict) else body
        spec = parse_form(body, form_id="Eventsignup-form")
        if not spec:
            raise CourtReserveError(refusal(body) or "sign-up form not found")
        spec.flags["payment_required"] = spec.get("RequireOnlinePayment")
        spec.flags["available"] = spec.get("SelectedReservation.AvailablePlaces")
        return spec

    def bookings_token(self) -> BookingsToken:
        url = f"{APP_HOST}/Online/Bookings/List/{self.org_id}"
        try:
            page = self.s.get(url, timeout=self.timeout).text
        except requests.RequestException as e:
            raise CourtReserveError(f"network error loading bookings page: {e}") from e
        mj, mr = JWT_RE.search(page), REQDATA_ATTR_RE.search(page)
        if not mj or not mr:
            if not looks_logged_in(page, url):
                raise LoginError("session expired")
            raise CourtReserveError("bookings page has no api token")
        try:
            claims = json.loads(base64.urlsafe_b64decode(mj.group(1).split(".")[1] + "==").decode())
            exp = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
        except Exception:
            exp = datetime.now(timezone.utc) + timedelta(minutes=30)
        return BookingsToken(jwt=mj.group(1), request_data=mr.group(1), expires_at=exp)

    def my_bookings(self, token: BookingsToken, start: date, end: date, tz: str | None, cancelled: bool = False) -> list[Booking]:
        headers = {
            "Accept": "application/json, text/plain, */*", "Content-Type": "application/json",
            "X-Requested-By": "AxiosReactRequest", "ReactSubmit": "true", "Origin": APP_HOST,
            "Referer": f"{APP_HOST}/Online/Bookings/List/{self.org_id}", "Authorization": f"Bearer {token.jwt}",
            "RequestData": token.request_data, "X-UI-CULTURE": "en-US", "X-UI-TIMEZONE": tz or "UTC",
        }
        params = {
            "OrganizationId": str(self.org_id), "OrgMemberIdsString": "", "BookingTypesString": ",".join(str(i) for i in range(1, 13)),
            "DatesString": "5", "FilterSelectedDates": "5", "SkipRows": "0", "IsCancelledView": "true" if cancelled else "false",
            "CustomDate_Start": self._mdy(start), "CustomDate_End": self._mdy(end),
        }
        try:
            r = self.s.get(f"{BACKEND_HOST}/api/my-bookings-portal/get-list",
                           params={"id": self.org_id, "requestData": token.request_data, "uiCulture": "en-US", **params},
                           headers=headers, timeout=self.timeout)
            if r.status_code == 401:
                raise LoginError("bookings token rejected")
            j = r.json()
        except requests.RequestException as e:
            raise CourtReserveError(f"network error loading bookings: {e}") from e
        except ValueError:
            raise CourtReserveError(f"unexpected bookings response (HTTP {r.status_code})")
        zone = ZoneInfo(tz) if tz else None
        out = []
        for x in j.get("Data") or []:
            try:
                st = datetime.fromisoformat(x["ReservationStartDateTime"]).replace(tzinfo=zone)
                en = datetime.fromisoformat(x["ReservationEndDateTime"]).replace(tzinfo=zone)
            except (KeyError, TypeError, ValueError):
                continue
            out.append(Booking(
                reservation_id=int(x.get("ReservationId") or 0), event_id=int(x["EventId"]) if x.get("EventId") else None,
                name=(x.get("EventName") or "").strip(), type_name=(x.get("TypeName") or "").strip(), start=st, end=en,
                courts=re.sub(r"\s+", " ", x.get("CourtsDisplay") or "").strip(), registered=bool(x.get("IsRegistered")),
                waitlisted=bool(x.get("IsWaitlisted") or x.get("IsWaitlistRow")), upcoming=bool(x.get("IsUpcoming")),
                cancelled=bool(x.get("IsCanceled")), button=(x.get("ButtonText") or "").strip(),
                members=[m.get("FullName", "") for m in x.get("ReservationMembersList") or []],
            ))
        out.sort(key=lambda b: b.start)
        return out

    def resolve_event(self, details_path: str) -> dict:
        page_url = details_path if details_path.startswith("http") else APP_HOST + details_path
        try:
            page = self.s.get(page_url, timeout=self.timeout).text
            m = re.search(r"fixUrl\('(https://events\.courtreserve\.com/Online/EventsApi/ApiDetails[^']+)'", page)
            body = page
            if m:
                r = self.s.get(html.unescape(m.group(1)), headers=self._xhr(page_url), timeout=self.timeout)
                try:
                    j = r.json()
                    body = (j.get("data") or j.get("html") or r.text) if isinstance(j, dict) else r.text
                except ValueError:
                    body = r.text
        except requests.RequestException as e:
            raise CourtReserveError(f"network error loading event: {e}") from e
        ev = re.search(r"eventId=(\d+)", body) or re.search(r"eventId=(\d+)", page)
        rid = re.search(r"(?:resId|reservationId)=(\d+)", details_path) or re.search(r"reservationId=(\d+)", body)
        num = re.search(r"/Details/\d+/([A-Z0-9]+)", details_path) or re.search(r"reservationNumber=([A-Z0-9]+)", body)
        opens = re.search(r"Registration opens in ([^<]{0,60}?)\s*<", body)
        return {"event_id": int(ev.group(1)) if ev else None, "reservation_id": int(rid.group(1)) if rid else None,
                "number": num.group(1) if num else None, "opens_in": opens.group(1).strip() if opens else None,
                "register_open": "SignUpToEvent/" in body, "waitlist_open": "SignUpToWaitingList/" in body}

    def event_waitlist_form(self, event_id: int, reservation_id: int) -> FormSpec:
        page_url = f"{APP_HOST}/Online/Events/SignUpToWaitingList/{self.org_id}?eventId={event_id}&reservationId={reservation_id}"
        try:
            page = self.s.get(page_url, timeout=self.timeout).text
        except requests.RequestException as e:
            raise CourtReserveError(f"network error loading event waitlist form: {e}") from e
        spec = parse_form(page, action_contains="SignUpToWaitingList")
        if not spec:
            if "/Account/LogIn" in page_url or "Account/LogIn" in page[:3000]:
                raise LoginError("session expired")
            raise CourtReserveError(refusal(page) or "event waitlist form not found")
        if spec.action.startswith("/"):
            spec.action = APP_HOST + spec.action
        spec.flags["auto_register"] = spec.get("AutoRegisterFromWaitList")
        return spec

    def submit_form(self, spec: FormSpec, overrides: dict[str, str], referer: str, dry_run: bool) -> dict:
        fields = spec.with_overrides(overrides)
        if dry_run or not ALLOW_SUBMIT:
            return {"dry_run": True, "action": spec.action, "fields": redacted(fields)}
        try:
            r = self.s.post(spec.action, data=fields, headers=self._xhr(referer), timeout=self.timeout)
            data = r.json() if "json" in r.headers.get("content-type", "") else None
        except requests.RequestException as e:
            raise CourtReserveError(f"network error submitting: {e}") from e
        return {"dry_run": False, "status": r.status_code, "json": data, "text": None if data else r.text[:500]}

    def booking_horizon(self, scheduler_id: int) -> tuple[datetime | None, datetime | None]:
        sched = self._scheduler_page(scheduler_id)
        return sched.book_min, sched.book_max

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
            start = ms_to_local(row.get("Start"), tz)
            end = ms_to_local(row.get("End"), tz)
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
                member_ids=[int(x) for x in (row.get("MemberIds") or []) if str(x).isdigit()],
            ))
        out.sort(key=lambda x: x.start)
        return out
