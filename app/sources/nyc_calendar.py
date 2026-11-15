import html
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.models import CandidateEvent
from app.pages import USER_AGENT
from app.sources.web import NO_TIME

log = logging.getLogger(__name__)

# The city's official NYC Events Calendar, through the NYC API Portal.
API_URL = "https://api.nyc.gov/calendar/search"
SOURCE = "nyc_calendar"
NY = ZoneInfo("America/New_York")
WINDOW_DAYS = 30
# The API gives 12 events per page, sorted by date.
MAX_PAGES = 5
MAX_CATEGORIES = 3
DATE_FORMAT = "%m/%d/%Y %I:%M %p"

# The search takes one category at a time. Two big categories are left out on purpose:
# "Parks & Recreation" (about 830 a month) copies the NYC Parks events we already read from Open Data,
# and "Street and Neighborhood" (about 650) is nearly all daily greenmarkets and traffic notices.
CATEGORIES = ["Cultural", "Featured", "Free", "General Events", "Holidays", "Tours", "Environment"]
# City business and services, not outings.
DROP_CATEGORIES = {
    "hearings and meetings",
    "city government office",
    "business & finance",
    "health & public safety",
    "volunteer",
    "benefits access",
    "hpd outreach event",
}
# Categories that say nothing about what the event is, so they don't show and can't save a kids-only event.
GENERAL_CATEGORIES = {"free", "general events", "special event", "parks & recreation", "kids and family"}
ROUTINE_WORDS = re.compile(r"\b(gridlock alert|alternate side|greenmarkets?|farmers? markets?|hhfm)\b", re.IGNORECASE)
FITNESS_WORDS = re.compile(r"\b(fitness|zumba|yoga|pilates|workout|circuit training|boot ?camp)\b", re.IGNORECASE)
KIDS_WORDS = re.compile(r"\b(story ?time|kids?|tots)\b", re.IGNORECASE)
# Addresses that don't name a place, like "Check website for route information".
PLACEHOLDER_PLACES = re.compile(r"^(check website|please see|citywide|various|tba|tbd)\b", re.IGNORECASE)
BOROUGHS = {"Mn": "Manhattan", "Bk": "Brooklyn", "Qn": "Queens", "Bx": "Bronx", "SI": "Staten Island"}


def fetch_calendar_events(client: httpx.Client, api_key: str, now: datetime) -> list[CandidateEvent]:
    if not api_key:
        log.warning("NYC_EVENTS_CALENDAR_API_KEY is not set, so NYC Events Calendar events are skipped")
        return []
    start = now.astimezone(NY)
    end = start + timedelta(days=WINDOW_DAYS)
    headers = {"Ocp-Apim-Subscription-Key": api_key, "User-Agent": USER_AGENT}
    records: list = []
    for category in CATEGORIES:
        params = {"startDate": start.strftime(DATE_FORMAT), "endDate": end.strftime(DATE_FORMAT), "sort": "DATE",
                  "categories": category}
        records += _category_records(client, headers, params)

    events = []
    for record in records:
        event = _to_event(record) if isinstance(record, dict) else None
        if event and now < event.start <= end and _worth_going_to(record, event):
            events.append(event)
    return _next_of_each(events)


def _category_records(client: httpx.Client, headers: dict, params: dict) -> list:
    records: list = []
    for page in range(1, MAX_PAGES + 1):
        try:
            response = client.get(API_URL, params={**params, "pageNumber": page}, headers=headers, timeout=30)
        except httpx.HTTPError as exc:
            log.warning("NYC Events Calendar request failed: %s", type(exc).__name__)
            break
        if response.status_code != 200:
            log.warning("NYC Events Calendar returned HTTP %s", response.status_code)
            break
        body = response.json()
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list) or not items:
            break
        records += items
        pagination = body.get("pagination") or {}
        if pagination.get("isLastPage") or page >= int(pagination.get("numPages") or 0):
            break
    return records


def _to_event(record: dict) -> CandidateEvent | None:
    name = _clean(record.get("name"))
    url = _clean(record.get("permalink"))
    try:
        start = datetime.fromisoformat(str(record.get("startDate")))
    except ValueError:
        return None
    if not name or not url.startswith("https://") or start.tzinfo is None:
        return None
    if record.get("allDay") is True:
        start = datetime.combine(start.astimezone(NY).date(), NO_TIME, tzinfo=NY)
    shown = [c for c in _categories(record) if c.lower() not in {"general events", "special event"}]
    return CandidateEvent(
        name=name,
        start=start,
        venue=_venue(record),
        city="New York",
        url=url,
        source=SOURCE,
        source_type="api",
        category=", ".join(shown[:MAX_CATEGORIES]) or None,
    )


def _venue(record: dict) -> str:
    for field in ("location", "address"):
        place = _clean(record.get(field))
        if place and not PLACEHOLDER_PLACES.match(place):
            return place
    boroughs = record.get("boroughs") or []
    return BOROUGHS.get(boroughs[0], "New York City") if len(boroughs) == 1 else "New York City"


def _categories(record: dict) -> list[str]:
    found = [_clean(part) for part in str(record.get("categories") or "").split(",")]
    return list(dict.fromkeys(part for part in found if part))


def _worth_going_to(record: dict, event: CandidateEvent) -> bool:
    if record.get("canceled") is True:
        return False
    categories = {c.lower() for c in _categories(record)}
    if categories & DROP_CATEGORIES:
        return False
    if "kids and family" in categories and not categories - GENERAL_CATEGORIES:
        return False
    return not (ROUTINE_WORDS.search(event.name) or FITNESS_WORDS.search(event.name) or KIDS_WORDS.search(event.name))


def _next_of_each(events: list[CandidateEvent]) -> list[CandidateEvent]:
    """An event listed for many dates, or under several categories, keeps only its next date."""
    kept: dict[str, CandidateEvent] = {}
    for event in sorted(events, key=lambda e: e.start):
        kept.setdefault(" ".join(re.sub(r"[^a-z0-9]+", " ", event.name.lower()).split()), event)
    return list(kept.values())


def _clean(text) -> str:
    # The API escapes slashes twice, so "Run/Walk" arrives as "Run\/Walk".
    without_tags = re.sub(r"<[^>]+>", " ", str(text or "").replace("\\/", "/"))
    return " ".join(html.unescape(without_tags).split())
