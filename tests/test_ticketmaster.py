import json
from datetime import datetime
from pathlib import Path

import httpx

from app.sources.ticketmaster import fetch_events
from tests.conftest import NY

NOW = datetime(2026, 11, 2, 12, tzinfo=NY)
FIXTURE = json.loads(Path("tests/fixtures/ticketmaster.json").read_text())


def client_returning(status, body):
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)))


def test_maps_fields():
    event = fetch_events(client_returning(200, FIXTURE), "k", NOW)[0]
    assert (event.name, event.venue, event.city, event.source_type) == ("Show", "Beacon Theatre", "New York", "api")
    assert (event.organizer, event.category) == ("Live Nation", "Music")
    assert event.start.tzinfo is not None
    assert event.url.startswith("https://")


def test_skips_records_without_venue_or_datetime_or_outside_nyc():
    assert [e.name for e in fetch_events(client_returning(200, FIXTURE), "k", NOW)] == ["Show"]


def test_error_status_returns_empty_list():
    assert fetch_events(client_returning(500, {}), "k", NOW) == []


def test_network_failure_returns_empty_list():
    def fail(request):
        raise httpx.ConnectError("down")

    assert fetch_events(httpx.Client(transport=httpx.MockTransport(fail)), "k", NOW) == []


def test_no_events_returns_empty_list():
    assert fetch_events(client_returning(200, {"page": {"totalElements": 0}}), "k", NOW) == []
