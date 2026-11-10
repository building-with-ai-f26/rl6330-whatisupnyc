import html
import logging
import re
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.models import CandidateEvent

log = logging.getLogger(__name__)

# NYC Parks Public Events, Upcoming 14 Days, from NYC Open Data. The city's Open Data Law
# makes it free to reuse, so long as apps name the source.
API_URL = "https://data.cityofnewyork.us/resource/w3wp-dpdi.json"
SOURCE = "nyc_parks"
ORGANIZER = "NYC Parks"
NY = ZoneInfo("America/New_York")
MAX_CATEGORIES = 3

# Regular classes from less-known names are left out unless the event is famous.
ROUTINE_CATEGORIES = {
    "exercise classes",
    "shape up nyc",
    "yoga & pilates classes",
    "dance classes",
    "recreation center programming",
    "outdoor fitness",
    "fitness",
}
ROUTINE_WORDS = re.compile(
    r"\b(zumba|yoga|pilates|boot ?camp|cardio|aerobics|tai chi|open play|after ?school|tiny tots|"
    r"walking session|sculpt|stretch|workout|fitness|swim)\b",
    re.IGNORECASE,
)
ONLINE_WORDS = re.compile(r"\b(virtual|online|zoom)\b", re.IGNORECASE)
CALLED_OFF = re.compile(r"^\s*(canceled|cancelled|postponed)\b", re.IGNORECASE)


def fetch_parks_events(
    client: httpx.Client, now: datetime, is_famous: Callable[[CandidateEvent], bool]
) -> list[CandidateEvent]:
    params = {"$limit": 1000, "$order": "starttime"}
    try:
        response = client.get(API_URL, params=params, timeout=30)
    except httpx.HTTPError as exc:
        log.warning("NYC Parks request failed: %s", type(exc).__name__)
        return []
    if response.status_code != 200:
        log.warning("NYC Parks returned HTTP %s", response.status_code)
        return []
    records = response.json()
    if not isinstance(records, list):
        return []
    events = []
    for record in records:
        event = _to_event(record) if isinstance(record, dict) else None
        if event and _worth_going_to(record, event, is_famous):
            events.append(event)
    return events


def _to_event(record: dict) -> CandidateEvent | None:
    name = html.unescape(record.get("title") or "").strip()
    venue = html.unescape(record.get("parknames") or record.get("location") or "").strip()
    url = (record.get("link") or {}).get("url") if isinstance(record.get("link"), dict) else None
    start_text = record.get("starttime")
    if not name or not venue or not url or not start_text:
        return None
    try:
        # The feed gives New York local time without a time zone.
        start = datetime.fromisoformat(start_text).replace(tzinfo=NY)
    except ValueError:
        return None
    return CandidateEvent(
        name=name,
        start=start,
        venue=venue,
        city="New York",
        url=re.sub(r"^http://", "https://", url),
        source=SOURCE,
        source_type="api",
        organizer=ORGANIZER,
        category=", ".join(_categories(record)[:MAX_CATEGORIES]) or None,
    )


def _categories(record: dict) -> list[str]:
    return [part.strip() for part in (record.get("categories") or "").split("|") if part.strip()]


def _worth_going_to(record: dict, event: CandidateEvent, is_famous: Callable[[CandidateEvent], bool]) -> bool:
    if "closed" in (record.get("registration_description") or "").lower():
        return False
    if ONLINE_WORDS.search(f"{event.name} {event.venue}") or CALLED_OFF.search(event.name):
        return False
    routine = ROUTINE_WORDS.search(event.name) or {c.lower() for c in _categories(record)} & ROUTINE_CATEGORIES
    return not routine or is_famous(event)
