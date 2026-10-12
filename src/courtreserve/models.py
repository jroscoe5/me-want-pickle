"""Data types returned by the CourtReserve client."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


class CourtReserveError(Exception):
    pass


class LoginError(CourtReserveError):
    pass


@dataclass
class ReservationType:
    id: int
    name: str
    max_time: int
    min_time: int
    min_players: int
    max_players: int


@dataclass
class BookingsToken:
    jwt: str
    request_data: str
    expires_at: datetime


@dataclass
class Booking:
    reservation_id: int
    event_id: int | None
    name: str
    type_name: str
    start: datetime
    end: datetime
    courts: str
    registered: bool
    waitlisted: bool
    upcoming: bool
    cancelled: bool
    button: str
    members: list[str]


@dataclass
class Member:
    org_member_id: int
    member_id: int | None
    name: str


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
    open_time: str | None = None
    close_time: str | None = None


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
    member_ids: list[int] = field(default_factory=list)


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
