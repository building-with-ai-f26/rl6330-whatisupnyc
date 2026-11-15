import html
import logging
import re
import string
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.models import CandidateEvent
from app.pages import USER_AGENT, robots_rules
from app.sites import Sites
from app.sources.web import NO_TIME

log = logging.getLogger(__name__)

# Many NYC museums, venues and campuses run their calendars on the WordPress plugin "The Events Calendar",
# which publishes the same public JSON feed on every site. The sites we read are listed in data/sites.json,
# each with an "events_api" entry. Each site is its own source, like "wp:movingimage.org".
SOURCE_PREFIX = "wp:"
NY = ZoneInfo("America/New_York")
WINDOW_DAYS = 30
PER_PAGE = 50
MAX_PAGES = 4
# Wait between requests, or longer if the site's robots.txt asks for a Crawl-delay.
PAUSE_SECONDS = 1.0
MAX_CATEGORIES = 3

CALLED_OFF = re.compile(r"^\s*(canceled|cancelled|postponed)\b", re.IGNORECASE)
VOLUNTEER_WORDS = re.compile(r"\b(volunteers?|it[’']?s my park|placepartner opportunity)\b", re.IGNORECASE)
MEMBERS_WORDS = re.compile(r"\bmembers?[- ]only\b", re.IGNORECASE)
# Programs for children, from the title or a category like "Family Programs".
KIDS_TITLE_WORDS = re.compile(r"\b(kids?|tots|storytime|story time|munchkins|for families)\b|^family\b", re.IGNORECASE)
KIDS_CATEGORY_WORDS = re.compile(r"\b(kids?|family|families|children)\b", re.IGNORECASE)
# Regular fitness classes, left out as in the NYC Parks feed.
FITNESS_WORDS = re.compile(r"\b(yoga|pilates|bodyroll|bodyweight|boot ?camp|workout|fitness)\b", re.IGNORECASE)
ONLINE_WORDS = re.compile(r"\b(virtual|online|zoom|remote)\b", re.IGNORECASE)
# Calendar entries that hold a date open rather than announce an event.
PLACEHOLDER_TITLES = re.compile(r"^(open for cocktails|open hours)$", re.IGNORECASE)
# Venue names that don't say where the event is, so the organization's name is used instead.
PLACEHOLDER_VENUES = re.compile(r"^(off-?site|various|off campus|tba|tbd)$", re.IGNORECASE)


def fetch_wp_events(
    client: httpx.Client, sites: Sites, now: datetime, sleep: Callable[[float], None] = time.sleep
) -> list[CandidateEvent]:
    events = []
    # One site at a time, so no site gets more than one request at once from us.
    for domain, feed in sites.events_feeds():
        try:
            events.extend(_site_events(client, domain, feed, now, sleep))
        except Exception:
            log.exception("WordPress feed failed: %s", domain)
    return events


def _site_events(client, domain: str, feed: dict, now: datetime, sleep) -> list[CandidateEvent]:
    url = feed["url"]
    rules = robots_rules(client, url)
    if rules is not None and not rules.can_fetch(USER_AGENT, url):
        log.warning("Robots rules block the events feed of %s", domain)
        return []
    pause = max(PAUSE_SECONDS, float((rules.crawl_delay(USER_AGENT) if rules else None) or 0))
    end = now + timedelta(days=WINDOW_DAYS)
    params = {
        "start_date": now.astimezone(NY).date().isoformat(),
        "end_date": end.astimezone(NY).date().isoformat(),
        "per_page": PER_PAGE,
    }
    records = []
    for page in range(1, MAX_PAGES + 1):
        sleep(pause)
        response = client.get(url, params={**params, "page": page}, headers={"User-Agent": USER_AGENT}, timeout=30)
        if response.status_code != 200:
            log.warning("WordPress feed of %s returned HTTP %s", domain, response.status_code)
            break
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("events"), list):
            log.warning("WordPress feed of %s was not an events list", domain)
            break
        records.extend(body["events"])
        if not body.get("next_rest_url"):
            break

    events = []
    for record in records:
        event = _to_event(record, domain, feed) if isinstance(record, dict) else None
        # Some sites ignore the date range, so it is checked here too.
        if event and now < event.start <= end and _worth_going_to(record, event, feed):
            events.append(event)
    return _next_of_each(events)


def _to_event(record: dict, domain: str, feed: dict) -> CandidateEvent | None:
    name = _title(record.get("title"))
    url = _clean(record.get("url"))
    start = _start(record)
    if not name or not url.startswith(("https://", "http://")) or start is None:
        return None
    venue_record = record.get("venue") if isinstance(record.get("venue"), dict) else {}
    venue = _clean(venue_record.get("venue"))
    if not venue or PLACEHOLDER_VENUES.match(venue):
        venue = feed["organization"]
    return CandidateEvent(
        name=name,
        start=start,
        venue=venue,
        city="New York",
        url=url,
        source=f"{SOURCE_PREFIX}{domain}",
        source_type="api",
        organizer=feed["organization"],
        category=", ".join(_categories(record)[:MAX_CATEGORIES]) or None,
    )


def _start(record: dict) -> datetime | None:
    try:
        # "start_date" is the site's local time, like "2026-11-21 15:30:00".
        local = datetime.strptime(str(record.get("start_date")), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    try:
        zone = ZoneInfo(record.get("timezone") or "America/New_York")
    except (ZoneInfoNotFoundError, ValueError):
        zone = NY
    if record.get("all_day") in (True, "true", "True"):
        local = datetime.combine(local.date(), NO_TIME)
    return local.replace(tzinfo=zone)


def _categories(record: dict) -> list[str]:
    found = record.get("categories")
    if not isinstance(found, list):
        return []
    return [name for c in found if isinstance(c, dict) and (name := _clean(c.get("name")))]


def _worth_going_to(record: dict, event: CandidateEvent, feed: dict) -> bool:
    categories = _categories(record)
    category_text = " | ".join(categories)
    only = {c.lower() for c in feed.get("only_categories", [])}
    if only and not only & {c.lower() for c in categories}:
        return False
    if str(record.get("status", "")).lower() in ("canceled", "cancelled") or CALLED_OFF.search(event.name):
        return False
    if PLACEHOLDER_TITLES.match(event.name):
        return False
    if ONLINE_WORDS.search(f"{event.name} | {event.venue} | {category_text}"):
        return False
    if MEMBERS_WORDS.search(f"{event.name} | {category_text}"):
        return False
    if VOLUNTEER_WORDS.search(f"{event.name} | {category_text}"):
        return False
    if KIDS_TITLE_WORDS.search(event.name) or KIDS_CATEGORY_WORDS.search(category_text):
        return False
    return not FITNESS_WORDS.search(event.name)


def _next_of_each(events: list[CandidateEvent]) -> list[CandidateEvent]:
    """A show with many performances becomes one entry: its next one."""
    kept: dict[str, CandidateEvent] = {}
    for event in sorted(events, key=lambda e: e.start):
        kept.setdefault(" ".join(re.sub(r"[^a-z0-9]+", " ", event.name.lower()).split()), event)
    return list(kept.values())


def _title(text) -> str:
    title = _clean(text)
    # Some sites write every title in capitals, like "HOLIDAY CONCERT AT SCANDINAVIA HOUSE".
    if title == title.upper() and len(title.split()) > 2:
        title = string.capwords(title)
    return title


def _clean(text) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", str(text or ""))
    return " ".join(html.unescape(without_tags).split())
