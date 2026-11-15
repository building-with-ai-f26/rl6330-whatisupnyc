import json
from datetime import datetime
from pathlib import Path

import httpx

from app.sources.nyc_calendar import CATEGORIES, MAX_PAGES, fetch_calendar_events
from tests.conftest import NY

NOW = datetime(2026, 11, 15, 8, tzinfo=NY)
FIXTURE = json.loads(Path("tests/fixtures/nyc_calendar.json").read_text())


def client(body=FIXTURE, status=200, requests=None):
    def handler(request):
        if requests is not None:
            requests.append(request)
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def with_pages(num_pages):
    return dict(FIXTURE, pagination=dict(FIXTURE["pagination"], numPages=num_pages, isLastPage=False))


def event_named(events, name):
    return next(event for event in events if event.name == name)


def test_maps_fields():
    event = event_named(fetch_calendar_events(client(), "k", NOW), "Macy's Parade Balloon Inflation")
    assert (event.venue, event.city, event.source, event.source_type) == (
        "Areas surrounding the Museum of Natural History", "New York", "nyc_calendar", "api"
    )
    assert event.url == "https://www1.nyc.gov/events/macys-parade-balloon-inflation/270016/1"
    assert event.start == datetime(2026, 11, 25, 13, tzinfo=NY)
    assert event.category == "Free, Holidays, Kids and Family"


def test_placeholder_address_falls_back_to_the_borough():
    event = event_named(fetch_calendar_events(client(), "k", NOW), "Macy's Thanksgiving Day Parade")
    assert event.venue == "Manhattan"


def test_keeps_events_worth_going_to_and_folds_repeats():
    # Dropped: city office notices, traffic alerts, recycling drop-offs, benefits outreach, a kids-only
    # story time, a greenmarket, a fitness class, and a cancelled talk. The exhibition's first date has
    # passed, so its next one is kept. Every category query returns the same items here, and each is kept once.
    events = fetch_calendar_events(client(), "k", NOW)
    assert [(event.name, event.start.day) for event in events] == [
        ("Proximity Exhibition", 20),
        ("Macy's Parade Balloon Inflation", 25),
        ("Macy's Thanksgiving Day Parade", 26),
    ]


def test_asks_for_each_useful_category_with_the_key_and_date_range():
    requests = []
    fetch_calendar_events(client(requests=requests), "secret", NOW)
    assert [r.url.params["categories"] for r in requests] == CATEGORIES
    first = requests[0]
    assert first.headers["Ocp-Apim-Subscription-Key"] == "secret"
    assert (first.url.params["startDate"], first.url.params["endDate"]) == ("11/15/2026 08:00 AM", "12/15/2026 08:00 AM")
    assert first.url.params["sort"] == "DATE"


def test_reads_pages_until_the_last_and_stops_at_the_page_limit():
    requests = []
    fetch_calendar_events(client(with_pages(3), requests=requests), "k", NOW)
    assert [r.url.params["pageNumber"] for r in requests[:4]] == ["1", "2", "3", "1"]
    requests = []
    fetch_calendar_events(client(with_pages(100), requests=requests), "k", NOW)
    assert len(requests) == MAX_PAGES * len(CATEGORIES)


def test_feed_failure_returns_nothing():
    assert fetch_calendar_events(client(status=503), "k", NOW) == []
    assert fetch_calendar_events(client(body={"message": "bad key"}), "k", NOW) == []


def test_missing_key_returns_nothing_without_a_request():
    requests = []
    assert fetch_calendar_events(client(requests=requests), "", NOW) == []
    assert requests == []
