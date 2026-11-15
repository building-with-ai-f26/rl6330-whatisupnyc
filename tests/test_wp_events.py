import json
from datetime import datetime
from pathlib import Path

import httpx

from app.scoring import OFFICIAL_FEED_POINTS
from app.sites import Sites
from app.sources.web import NO_TIME
from app.sources.wp_events import MAX_PAGES, fetch_wp_events
from tests.conftest import NY

NOW = datetime(2026, 11, 14, 12, tzinfo=NY)
FIXTURE = json.loads(Path("tests/fixtures/wp_events.json").read_text())
MOMI_FEED = "https://movingimage.org/wp-json/tribe/events/v1/events"


def site(domain, feed_url, organization, automated_access="allowed", **feed):
    return {"domain": domain, "automated_access": automated_access, "reliable": True,
            "events_api": {"url": feed_url, "organization": organization, **feed}}


MOMI = site("movingimage.org", MOMI_FEED, "Museum of the Moving Image")


def client(routes, requests=None, robots=None):
    """routes maps a feed URL (without query) to a JSON body, or to a status code."""

    def handler(request):
        if requests is not None:
            requests.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots) if robots else httpx.Response(404)
        body = routes.get(str(request.url.copy_with(query=None)))
        if body is None:
            return httpx.Response(404)
        if isinstance(body, int):
            return httpx.Response(body)
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def fetch(entries, routes, sleeps=None, **kwargs):
    sleeps = [] if sleeps is None else sleeps
    return fetch_wp_events(client(routes, **kwargs), Sites(entries), NOW, sleep=sleeps.append)


def event_named(events, name):
    return next(event for event in events if event.name == name)


def test_maps_fields():
    event = event_named(fetch([MOMI], {MOMI_FEED: FIXTURE}), "Paths of Glory")
    assert (event.venue, event.city, event.url) == (
        "Redstone Theater", "New York", "https://movingimage.org/event/paths-of-glory-2026/"
    )
    assert (event.source, event.source_type, event.organizer, event.category) == (
        "wp:movingimage.org", "api", "Museum of the Moving Image", "Screening"
    )
    assert event.start == datetime(2026, 11, 21, 15, 30, tzinfo=NY)


def test_missing_venue_falls_back_to_the_organization():
    event = event_named(fetch([MOMI], {MOMI_FEED: FIXTURE}), "Rock ‘n’ Roll High School")
    assert event.venue == "Museum of the Moving Image"


def test_all_day_event_has_no_time():
    event = event_named(fetch([MOMI], {MOMI_FEED: FIXTURE}), "Made in Clay Holiday Sale")
    assert event.start == datetime(2026, 11, 23, NO_TIME.hour, NO_TIME.minute, tzinfo=NY)


def test_all_caps_title_is_title_cased():
    names = [event.name for event in fetch([MOMI], {MOMI_FEED: FIXTURE})]
    assert "The Squirrel | Screening & Director Talk" in names


def test_keeps_events_worth_going_to_and_folds_repeats():
    # Dropped: cancelled by title or status, volunteer shift, members-only, kids-only, a placeholder,
    # online, a fitness class, already past, and beyond the 30-day window. Tristan & Yseult repeats,
    # so only its next performance is kept.
    events = fetch([MOMI], {MOMI_FEED: FIXTURE})
    assert [(event.name, event.start.day) for event in events] == [
        ("Tristan & Yseult", 17),
        ("Paths of Glory", 21),
        ("Rock ‘n’ Roll High School", 21),
        ("The Squirrel | Screening & Director Talk", 22),
        ("Made in Clay Holiday Sale", 23),
    ]


def test_campus_site_keeps_only_chosen_categories():
    hunter_feed = "https://www.hunter.cuny.edu/wp-json/tribe/events/v1/events"
    hunter = site("hunter.cuny.edu", hunter_feed, "Hunter College", only_categories=["Lectures"])
    events = [e for e in fetch([hunter], {hunter_feed: FIXTURE})]
    assert events == []
    lecture = dict(FIXTURE["events"][0], categories=[{"name": "Lectures", "slug": "lectures"}])
    events = fetch([hunter], {hunter_feed: {"events": [lecture]}})
    assert [(e.name, e.source) for e in events] == [("Paths of Glory", "wp:hunter.cuny.edu")]


def test_reads_next_pages_and_stops_at_the_page_limit():
    page = {"events": [FIXTURE["events"][0]], "next_rest_url": MOMI_FEED + "?page=2"}
    requests = []
    fetch([MOMI], {MOMI_FEED: page}, requests=requests)
    pages = [r.url.params.get("page") for r in requests if r.url.path != "/robots.txt"]
    assert pages == [str(n) for n in range(1, MAX_PAGES + 1)]
    assert requests[1].url.params["start_date"] == "2026-11-14"
    assert requests[1].url.params["end_date"] == "2026-12-14"


def test_stops_when_there_is_no_next_page():
    requests = []
    fetch([MOMI], {MOMI_FEED: FIXTURE}, requests=requests)
    assert [r.url.params.get("page") for r in requests if r.url.path != "/robots.txt"] == ["1"]


def test_one_site_failing_does_not_stop_the_others():
    bad_feed = "https://www.zincbar.com/wp-json/tribe/events/v1/events"
    zinc = site("zincbar.com", bad_feed, "Zinc Bar")
    events = fetch([zinc, MOMI], {bad_feed: 503, MOMI_FEED: FIXTURE})
    assert {event.source for event in events} == {"wp:movingimage.org"}
    assert fetch([zinc], {bad_feed: {"not": "events"}}) == []


def test_site_not_allowed_in_sites_json_is_not_read():
    requests = []
    blocked = site("movingimage.org", MOMI_FEED, "Museum of the Moving Image", automated_access="prohibited")
    assert fetch([blocked], {MOMI_FEED: FIXTURE}, requests=requests) == []
    assert requests == []


def test_respects_robots_rules_and_crawl_delay():
    requests = []
    assert fetch([MOMI], {MOMI_FEED: FIXTURE}, requests=requests, robots="User-agent: *\nDisallow: /wp-json/") == []
    assert [r.url.path for r in requests] == ["/robots.txt"]
    sleeps = []
    fetch([MOMI], {MOMI_FEED: FIXTURE}, sleeps=sleeps, robots="User-agent: *\nCrawl-delay: 10")
    assert sleeps == [10]


def test_every_feed_in_sites_json_has_points():
    entries = json.loads(Path("data/sites.json").read_text())
    feeds = [f"wp:{e['domain']}" for e in entries if e.get("events_api")]
    assert feeds
    assert [feed for feed in feeds if feed not in OFFICIAL_FEED_POINTS] == []
