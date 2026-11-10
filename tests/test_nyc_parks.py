import json
from datetime import datetime
from pathlib import Path

import httpx

from app.sources.nyc_parks import fetch_parks_events
from tests.conftest import NY

NOW = datetime(2026, 11, 2, 12, tzinfo=NY)
FIXTURE = json.loads(Path("tests/fixtures/nyc_parks.json").read_text())


def client_returning(status, body):
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)))


def famous_lincoln_center(event):
    return "lincoln center" in event.name.lower()


def names(events):
    return [event.name for event in events]


def test_maps_fields():
    event = fetch_parks_events(client_returning(200, FIXTURE), NOW, famous_lincoln_center)[0]
    assert (event.name, event.venue, event.city, event.source_type) == (
        "Le Carrousel Extravaganza", "Bryant Park", "New York", "api"
    )
    assert (event.organizer, event.category, event.source) == ("NYC Parks", "Art, Kids, Holiday", "nyc_parks")
    assert event.start == datetime(2026, 11, 7, 14, tzinfo=NY)
    assert event.url == "https://www.nycgovparks.org/events/2026/11/07/le-carrousel"


def test_keeps_events_worth_going_to_and_drops_the_rest():
    events = fetch_parks_events(client_returning(200, FIXTURE), NOW, famous_lincoln_center)
    assert names(events) == ["Le Carrousel Extravaganza", "Fall Foliage Walk", "Sunset Yoga at Lincoln Center"]


def test_uses_location_when_park_name_is_missing():
    events = fetch_parks_events(client_returning(200, FIXTURE), NOW, famous_lincoln_center)
    assert events[1].venue == "Meet at the Dairy"


def test_routine_class_kept_only_when_famous():
    assert "Sunset Yoga at Lincoln Center" in names(fetch_parks_events(client_returning(200, FIXTURE), NOW, famous_lincoln_center))
    assert "Sunset Yoga at Lincoln Center" not in names(fetch_parks_events(client_returning(200, FIXTURE), NOW, lambda e: False))


def test_canceled_events_are_dropped():
    events = fetch_parks_events(client_returning(200, FIXTURE), NOW, lambda e: False)
    assert not any("Harbor Tour" in name for name in names(events))


def test_coded_characters_become_text():
    record = {**FIXTURE[2], "title": "Caf&#233; Tour &amp; Talk"}
    assert names(fetch_parks_events(client_returning(200, [record]), NOW, lambda e: False)) == ["Café Tour & Talk"]


def test_error_status_returns_empty_list():
    assert fetch_parks_events(client_returning(500, {}), NOW, lambda e: False) == []


def test_unexpected_body_returns_empty_list():
    assert fetch_parks_events(client_returning(200, {"error": "x"}), NOW, lambda e: False) == []


def test_network_failure_returns_empty_list():
    def fail(request):
        raise httpx.ConnectError("down")

    assert fetch_parks_events(httpx.Client(transport=httpx.MockTransport(fail)), NOW, lambda e: False) == []
