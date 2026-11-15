from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.sources.nyu_engage import fetch_engage_events
from tests.conftest import NY

NOW = datetime(2026, 11, 2, 12, tzinfo=NY)
FIXTURE = Path("tests/fixtures/nyu_engage.rss").read_bytes()


def client_returning(status, body=FIXTURE):
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, content=body)))


def names(events):
    return [event.name for event in events]


def test_maps_fields():
    event = fetch_engage_events(client_returning(200), NOW)[0]
    assert (event.name, event.venue, event.city, event.source_type) == (
        "Studio Tour @ Conran Design Group", "Tisch School of the Arts", "New York", "api"
    )
    assert (event.organizer, event.source) == ("AStudio at NYU", "nyu_engage")
    assert event.start == datetime(2026, 11, 5, 19, 30, tzinfo=timezone.utc)
    assert event.url == "https://engage.nyu.edu/event/1001"


def test_category_skips_engage_theme_codes_and_keeps_three():
    event = fetch_engage_events(client_returning(200), NOW)[0]
    assert event.category == "Arts, Creative (Design, Aesthetic), Professional & Career Development"


def test_keeps_events_worth_going_to_and_drops_the_rest():
    # Dropped: club business, cancelled (by status or title), online, already past, and no location.
    assert names(fetch_engage_events(client_returning(200), NOW)) == [
        "Studio Tour @ Conran Design Group", "Southeast Asian Festival"
    ]


def test_feed_failure_returns_nothing():
    assert fetch_engage_events(client_returning(503), NOW) == []
    assert fetch_engage_events(client_returning(200, b"<html>not a feed"), NOW) == []
