import json
from datetime import datetime
from pathlib import Path

import httpx

from app.sources.nps import MAX_PAGES, fetch_nps_events
from tests.conftest import NY

NOW = datetime(2026, 11, 15, 12, tzinfo=NY)
FIXTURE = json.loads(Path("tests/fixtures/nps.json").read_text())


def client(body=FIXTURE, status=200, requests=None):
    def handler(request):
        if requests is not None:
            requests.append(request)
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def event_named(events, name):
    return next(event for event in events if event.name == name)


def test_maps_fields():
    event = event_named(fetch_nps_events(client(), "k", NOW), "Jamaica Bay Book Club")
    assert (event.venue, event.city, event.organizer) == (
        "Jamaica Bay Wildlife Refuge Visitor Center", "New York", "Gateway National Recreation Area"
    )
    assert (event.source, event.source_type, event.category) == ("nps", "api", "Free")
    assert event.url == "https://www.nps.gov/planyourvisit/event-details.htm?id=0275DC25-BA79-B5C3-F122BBA791E81BF1"
    assert event.start == datetime(2026, 11, 20, 14, tzinfo=NY)


def test_long_or_missing_location_falls_back_to_the_park():
    events = fetch_nps_events(client(), "k", NOW)
    assert event_named(events, "House Tour").venue == "Theodore Roosevelt Birthplace National Historic Site"
    assert event_named(events, "A Partnership that Forged a New Nation").venue == "Hamilton Grange National Memorial"


def test_free_and_types_go_in_the_category():
    event = event_named(fetch_nps_events(client(), "k", NOW), "House Tour")
    assert event.category == "Free, Guided Tour"


def test_uses_the_next_time_that_has_not_passed_or_been_cancelled():
    events = fetch_nps_events(client(), "k", NOW)
    # House Tour runs at 11, 12, 2 and 3 today; it is noon, so 2 PM is next.
    assert event_named(events, "House Tour").start == datetime(2026, 11, 15, 14, tzinfo=NY)
    # Hangar B is cancelled on Nov 21, so the next tour is Nov 28.
    assert event_named(events, "Hangar B Ranger Tour").start == datetime(2026, 11, 28, 10, 30, tzinfo=NY)


def test_keeps_nyc_events_and_folds_repeats():
    # Dropped: two Sandy Hook events in New Jersey and a Junior Ranger kids' program.
    # How To Hibernate Walk is listed twice, so only the first is kept.
    events = fetch_nps_events(client(), "k", NOW)
    assert [(event.name, event.start.day) for event in events] == [
        ("House Tour", 15),
        ("Jamaica Bay Book Club", 20),
        ("How To Hibernate Walk", 21),
        ("A Partnership that Forged a New Nation", 27),
        ("Hangar B Ranger Tour", 28),
        ("Stargazing: Floyd Bennett Field with Amateur Astronomers Association", 12),
    ]


def test_reads_pages_until_it_has_the_total_and_stops_at_the_page_limit():
    requests = []
    fetch_nps_events(client(dict(FIXTURE, total="25"), requests=requests), "k", NOW)
    assert [r.url.params["pageNumber"] for r in requests] == ["1", "2", "3"]
    assert requests[0].url.params["dateStart"] == "2026-11-15"
    assert requests[0].url.params["dateEnd"] == "2026-12-15"
    requests = []
    fetch_nps_events(client(dict(FIXTURE, total="100000"), requests=requests), "k", NOW)
    assert len(requests) == MAX_PAGES


def test_feed_failure_returns_nothing():
    assert fetch_nps_events(client(status=503), "k", NOW) == []
    assert fetch_nps_events(client(body={"errors": ["bad"]}), "k", NOW) == []


def test_missing_key_returns_nothing_without_a_request():
    requests = []
    assert fetch_nps_events(client(requests=requests), "", NOW) == []
    assert requests == []
