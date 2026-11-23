import html
import logging
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.models import CandidateEvent
from app.pages import USER_AGENT, clean_image_url
from app.sources.web import NO_TIME

log = logging.getLogger(__name__)

# The National Park Service's public data API. These are the park units in and around New York Harbor:
# Gateway, Statue of Liberty, Governors Island, African Burial Ground, Castle Clinton, Federal Hall,
# General Grant, Hamilton Grange, Theodore Roosevelt Birthplace, and Stonewall.
API_URL = "https://developer.nps.gov/api/v1/events"
PARK_CODES = "gate,stli,gois,afbg,cacl,fedh,gegr,hagr,thrb,stwa"
EVENT_PAGE = "https://www.nps.gov/planyourvisit/event-details.htm?id={id}"
SOURCE = "nps"
NY = ZoneInfo("America/New_York")
WINDOW_DAYS = 30
PAGE_SIZE = 200
MAX_PAGES = 4
MAX_CATEGORIES = 3
# Some locations are directions, like "The tour is inside the brownstone at 28 E 20th St, ...".
# Those read badly as a venue, so the park's name is used instead.
MAX_VENUE_WORDS = 8

# Gateway's Sandy Hook unit is in New Jersey.
NOT_NYC = re.compile(r"\b(sandy hook|fort hancock|new jersey|nj)\b", re.IGNORECASE)
KIDS_WORDS = re.compile(r"\b(junior ranger|kids?)\b", re.IGNORECASE)
CALLED_OFF = re.compile(r"^\s*(canceled|cancelled|postponed)\b", re.IGNORECASE)


def fetch_nps_events(client: httpx.Client, api_key: str, now: datetime) -> list[CandidateEvent]:
    if not api_key:
        log.warning("NPS_API_KEY is not set, so National Park Service events are skipped")
        return []
    end = now + timedelta(days=WINDOW_DAYS)
    params = {
        "parkCode": PARK_CODES,
        "dateStart": now.astimezone(NY).date().isoformat(),
        "dateEnd": end.astimezone(NY).date().isoformat(),
        "pageSize": PAGE_SIZE,
        "api_key": api_key,
    }
    records: list = []
    for page in range(1, MAX_PAGES + 1):
        try:
            response = client.get(API_URL, params={**params, "pageNumber": page},
                                  headers={"User-Agent": USER_AGENT}, timeout=60)
        except httpx.HTTPError as exc:
            log.warning("National Park Service request failed: %s", type(exc).__name__)
            break
        if response.status_code != 200:
            log.warning("National Park Service returned HTTP %s", response.status_code)
            break
        body = response.json()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or not data:
            break
        records.extend(data)
        try:
            total = int(body.get("total") or 0)
        except ValueError:
            total = 0
        if len(records) >= total:
            break

    events = []
    for record in records:
        event = _to_event(record, now, end) if isinstance(record, dict) else None
        if event and _worth_going_to(record, event):
            events.append(event)
    return _next_of_each(events)


def _next_of_each(events: list[CandidateEvent]) -> list[CandidateEvent]:
    """A program listed more than once, like a weekly walk, keeps only its next date."""
    kept: dict[str, CandidateEvent] = {}
    for event in sorted(events, key=lambda e: e.start):
        kept.setdefault(" ".join(re.sub(r"[^a-z0-9]+", " ", event.name.lower()).split()), event)
    return list(kept.values())


def _to_event(record: dict, now: datetime, end: datetime) -> CandidateEvent | None:
    name = _clean(record.get("title"))
    park = _clean(record.get("parkfullname"))
    event_id = _clean(record.get("id"))
    start = _next_start(record, now, end)
    if not name or not park or not event_id or start is None:
        return None
    location = _clean(record.get("location"))
    venue = location if location and len(location.split()) <= MAX_VENUE_WORDS else park
    return CandidateEvent(
        name=name,
        start=start,
        venue=venue,
        city="New York",
        url=EVENT_PAGE.format(id=event_id),
        source=SOURCE,
        source_type="api",
        organizer=park,
        category=", ".join(_categories(record)[:MAX_CATEGORIES]) or None,
        image_url=_image(record.get("images")),
    )


def _image(images) -> str | None:
    """The event's first picture. NPS lists them relative to nps.gov."""
    for image in images or []:
        if isinstance(image, dict) and (url := clean_image_url(image.get("url"), "https://www.nps.gov")):
            return url
    return None


def _next_start(record: dict, now: datetime, end: datetime) -> datetime | None:
    """The first listed date and time that is still ahead and not cancelled."""
    cancelled = {_day(text) for text in record.get("cancelleddatelist") or []}
    times = [_clock(t.get("timestart")) for t in record.get("times") or [] if isinstance(t, dict)]
    if record.get("isallday") in (True, "true") or not any(times):
        times = [NO_TIME]
    starts = []
    for text in record.get("dates") or []:
        day = _day(text)
        if day is None or day in cancelled:
            continue
        starts += [datetime.combine(day, at, tzinfo=NY) for at in times if at]
    upcoming = [start for start in starts if now < start <= end]
    return min(upcoming, default=None)


def _day(text) -> date | None:
    for pattern in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(str(text), pattern).date()
        except ValueError:
            continue
    return None


def _clock(text) -> time | None:
    try:
        return datetime.strptime(str(text).strip(), "%I:%M %p").time()
    except ValueError:
        return None


def _categories(record: dict) -> list[str]:
    # Every NPS program in these parks is free, and the data says so, so readers can be told.
    free = ["Free"] if record.get("isfree") in (True, "true") else []
    types = [_clean(t) for t in record.get("types") or [] if _clean(t) and _clean(t) != "Other"]
    return free + types


def _worth_going_to(record: dict, event: CandidateEvent) -> bool:
    tags = " | ".join(str(tag) for tag in record.get("tags") or [])
    if NOT_NYC.search(f"{event.name} | {_clean(record.get('location'))} | {tags}"):
        return False
    if CALLED_OFF.search(event.name):
        return False
    return not KIDS_WORDS.search(f"{event.name} | {tags}")


def _clean(text) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", str(text or ""))
    return " ".join(html.unescape(without_tags).split())
