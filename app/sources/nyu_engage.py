import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

import httpx

from app.models import CandidateEvent

log = logging.getLogger(__name__)

# NYU Engage's public events feed: every public NYU event in the next 30 days.
# Robots rules allow this feed but block Engage's /api/ pages, so we only read the feed.
FEED_URL = "https://engage.nyu.edu/events.rss"
SOURCE = "nyu_engage"
EVENTS_NS = "{events}"
MAX_CATEGORIES = 3

# A club's own business, not something a newcomer would go to.
ROUTINE_WORDS = re.compile(
    r"\b(meetings?|weekly|chapter|e-?board|committee|general body|gbm|small group|large group|connect group|"
    r"bible study|fellowship|prayer|mass|rehearsals?|practices?|tabling|drop-in|recruitment|office hours|"
    r"learning group|study (?:jam|group|hall))\b",
    re.IGNORECASE,
)
ONLINE_WORDS = re.compile(r"\b(virtual|online|zoom|teams)\b|https?://", re.IGNORECASE)
CALLED_OFF = re.compile(r"\b(canceled|cancelled|postponed)\b", re.IGNORECASE)
# Engage's own theme codes, like "ThoughtfulLearning", aren't meant for readers.
THEME_CODE = re.compile(r"^[A-Z][a-z]+(?:[A-Z][a-z]+)+$")


def fetch_engage_events(client: httpx.Client, now: datetime) -> list[CandidateEvent]:
    try:
        response = client.get(FEED_URL, timeout=30)
    except httpx.HTTPError as exc:
        log.warning("NYU Engage request failed: %s", type(exc).__name__)
        return []
    if response.status_code != 200:
        log.warning("NYU Engage returned HTTP %s", response.status_code)
        return []
    try:
        items = ET.fromstring(response.content).findall("./channel/item")
    except ET.ParseError:
        log.warning("NYU Engage feed was not valid XML")
        return []
    events = []
    for item in items:
        event = _to_event(item)
        if event and event.start > now and _worth_going_to(item, event):
            events.append(event)
    return events


def _to_event(item: ET.Element) -> CandidateEvent | None:
    name = _clean(item.findtext("title"))
    venue = _clean(item.findtext(f"{EVENTS_NS}location"))
    url = _clean(item.findtext("link"))
    start_text = _clean(item.findtext(f"{EVENTS_NS}start"))
    if not name or not venue or not url or not start_text:
        return None
    try:
        # The feed gives times in GMT, like "Thu, 12 Nov 2026 19:30:00 GMT".
        start = parsedate_to_datetime(start_text)
    except (TypeError, ValueError):
        return None
    if start.tzinfo is None:
        return None
    categories = [_clean(c.text) for c in item.findall("category")]
    readable = [c for c in categories if c and not THEME_CODE.match(c)]
    return CandidateEvent(
        name=name,
        start=start,
        venue=venue,
        city="New York",
        url=url,
        source=SOURCE,
        source_type="api",
        organizer=_clean(item.findtext(f"{EVENTS_NS}host")) or None,
        category=", ".join(readable[:MAX_CATEGORIES]) or None,
    )


def _worth_going_to(item: ET.Element, event: CandidateEvent) -> bool:
    if _clean(item.findtext(f"{EVENTS_NS}status")).lower() == "cancelled" or CALLED_OFF.search(event.name):
        return False
    if ONLINE_WORDS.search(event.venue):
        return False
    return not ROUTINE_WORDS.search(event.name)


def _clean(text: str | None) -> str:
    return " ".join(html.unescape(text or "").split())
