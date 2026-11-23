import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.pages import clean_image_url
from app.models import CandidateEvent

log = logging.getLogger(__name__)

API_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
SEARCH_WINDOW = timedelta(days=21)
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

NYC_CITIES = {
    "new york",
    "new york city",
    "manhattan",
    "brooklyn",
    "queens",
    "bronx",
    "the bronx",
    "staten island",
    "flushing",
    "long island city",
    "astoria",
    "forest hills",
    "jamaica",
    "corona",
}


def fetch_events(client: httpx.Client, api_key: str, now: datetime, size: int = 100) -> list[CandidateEvent]:
    params = {
        "apikey": api_key,
        "stateCode": "NY",
        "startDateTime": now.astimezone(timezone.utc).strftime(TIME_FORMAT),
        "endDateTime": (now + SEARCH_WINDOW).astimezone(timezone.utc).strftime(TIME_FORMAT),
        # Sorting by date fills the page with the next day or two, so sort by relevance.
        "sort": "relevance,desc",
        "size": size,
    }
    try:
        response = client.get(API_URL, params=params, timeout=30)
    except httpx.HTTPError as exc:
        log.warning("Ticketmaster request failed: %s", type(exc).__name__)
        return []
    if response.status_code != 200:
        log.warning("Ticketmaster returned HTTP %s", response.status_code)
        return []
    records = response.json().get("_embedded", {}).get("events", [])
    return [event for event in map(_to_event, records) if event]


def _to_event(record: dict) -> CandidateEvent | None:
    start = record.get("dates", {}).get("start", {}).get("dateTime")
    venues = record.get("_embedded", {}).get("venues", [])
    if not start or not venues or not record.get("name") or not record.get("url"):
        return None
    venue = venues[0]
    city = venue.get("city", {}).get("name", "")
    if not venue.get("name") or city.lower() not in NYC_CITIES:
        return None
    classifications = record.get("classifications") or [{}]
    return CandidateEvent(
        name=record["name"],
        start=datetime.fromisoformat(start.replace("Z", "+00:00")),
        venue=venue["name"],
        city=city,
        url=record["url"],
        source="ticketmaster",
        source_type="api",
        organizer=record.get("promoter", {}).get("name"),
        category=classifications[0].get("segment", {}).get("name"),
        image_url=_image(record.get("images")),
    )


def _image(images) -> str | None:
    """A wide picture big enough for a card: 16:9 from 640 px wide, smallest such; else the widest."""
    found = [i for i in images or [] if isinstance(i, dict) and clean_image_url(i.get("url"))]
    wide = [i for i in found if i.get("ratio") == "16_9" and (i.get("width") or 0) >= 640]
    if wide:
        return clean_image_url(min(wide, key=lambda i: i.get("width") or 0)["url"])
    if found:
        return clean_image_url(max(found, key=lambda i: i.get("width") or 0)["url"])
    return None
