import json
from datetime import datetime
from pathlib import Path

import httpx

from app import db
from app.ai import AIUnavailable
from app.pipeline import PipelineDeps, run_pipeline
from app.scoring import Entry, Reputable
from app.sources.web import SearchHit
from tests.conftest import NY, make_event
from tests.fakes import FakeAI, FakeTavily

NOW = datetime(2026, 11, 2, 12, tzinfo=NY)
FIXTURE = json.loads(Path("tests/fixtures/ticketmaster.json").read_text())
REPUTABLE = Reputable([Entry("Beacon Theatre", [], "beacon.example")])


def jsonld(name, venue="Other Hall"):
    return (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "Event", "name": name, "startDate": "2026-11-07T20:00:00-05:00",
                      "location": {"name": venue, "address": {"addressLocality": "Brooklyn"}}})
        + f"</script><p>{name} on Sat, Nov 7 at {venue}</p>"
    )


def deps(sites, pages=None, ticketmaster=FIXTURE, tavily=None, ai=None, parks=None, engage=None):
    def handler(request):
        if request.url.host == "app.ticketmaster.com":
            return httpx.Response(200, json=ticketmaster)
        if request.url.host == "data.cityofnewyork.us":
            return httpx.Response(200, json=parks or [])
        if str(request.url) == "https://engage.nyu.edu/events.rss" and engage:
            return httpx.Response(200, content=engage)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        html = (pages or {}).get(str(request.url))
        return httpx.Response(200, text=html) if html else httpx.Response(404)

    return PipelineDeps(
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        tavily=tavily or FakeTavily(),
        ai=ai or FakeAI(),
        sites=sites,
        reputable=REPUTABLE,
        ticketmaster_key="k",
    )


EMPTY_TM = {"page": {"totalElements": 0}}


def test_reputable_api_event_is_saved_eligible(conn, sites):
    summary = run_pipeline(conn, deps(sites), NOW)
    assert [e.event.name for e in db.eligible_events(conn, NOW)] == ["Show"]
    assert summary.eligible == 1


def test_pipeline_logs_dropped_with_reason(conn, sites):
    tavily = FakeTavily([SearchHit("https://reliable.example/bad", "t", "c")])
    html = jsonld("Wrong Date").replace("Sat, Nov 7", "Sat, Nov 21")
    d = deps(sites, {"https://reliable.example/bad": html}, EMPTY_TM, tavily)
    summary = run_pipeline(conn, d, NOW)
    assert summary.dropped == 1
    assert db.dropped_reasons(conn) == {"date not confirmed on page": 1}


def test_event_on_reliable_page_is_eligible_through_coverage(conn, sites):
    tavily = FakeTavily([SearchHit("https://reliable.example/a", "t", "c")])
    d = deps(sites, {"https://reliable.example/a": jsonld("Indie Night")}, EMPTY_TM, tavily)
    run_pipeline(conn, d, NOW)
    assert db.eligible_events(conn, NOW)[0].score.total == 10


def test_event_only_on_listing_site_is_excluded(conn, sites):
    tavily = FakeTavily([SearchHit("https://listing.example/a", "t", "c")])
    d = deps(sites, {"https://listing.example/a": jsonld("Unknown Party")}, EMPTY_TM, tavily)
    assert run_pipeline(conn, d, NOW).eligible == 0
    assert db.eligible_events(conn, NOW) == []


def test_pipeline_survives_one_source_failing(conn, sites):
    class BrokenTavily(FakeTavily):
        def search(self, query, include_domains):
            raise RuntimeError("search is down")

    assert run_pipeline(conn, deps(sites, tavily=BrokenTavily()), NOW).passed > 0


def test_pipeline_merges_same_event_across_sources(conn, sites):
    tm = {"_embedded": {"events": [FIXTURE["_embedded"]["events"][0]]}}
    tavily = FakeTavily([SearchHit("https://reliable.example/s", "t", "c")])
    html = jsonld("Show", "Beacon Theatre").replace("Brooklyn", "New York")
    d = deps(sites, {"https://reliable.example/s": html}, tm, tavily)
    run_pipeline(conn, d, NOW)
    events = db.eligible_events(conn, NOW)
    assert len(events) == 1
    assert events[0].score.total == 50
    assert events[0].event.source_type == "api"


def test_rerun_keeps_event_id_and_makes_no_new_ai_calls(conn, sites):
    tavily = FakeTavily([SearchHit("https://reliable.example/a", "t", "c")])
    ai = FakeAI()
    d = deps(sites, {"https://reliable.example/a": jsonld("Indie Night")}, EMPTY_TM, tavily, ai)
    run_pipeline(conn, d, NOW)
    first = db.eligible_events(conn, NOW)[0].id
    calls = ai.calls
    run_pipeline(conn, d, NOW)
    assert db.eligible_events(conn, NOW)[0].id == first
    assert ai.calls == calls


def test_pasted_post_adds_interest_points(conn, sites):
    tm = {"_embedded": {"events": [FIXTURE["_embedded"]["events"][0]]}}
    db.save_paste(conn, "https://www.tiktok.com/@a/1", "loved it", make_event(url="https://reliable.example/p", source_type="pasted"))
    d = deps(sites, {"https://reliable.example/p": "<p>Show on Sat, Nov 7 at Beacon Theatre</p>"}, tm)
    run_pipeline(conn, d, NOW)
    assert db.eligible_events(conn, NOW)[0].score.total == 55


def test_ai_outage_still_saves_events(conn, sites):
    d = deps(sites, ai=FakeAI(error=AIUnavailable("busy")))
    assert run_pipeline(conn, d, NOW).eligible == 1


def test_sentiment_negative_excludes_shortlisted_event(conn, sites):
    tavily = FakeTavily(
        [SearchHit("https://reliable.example/r", "t", "A dreadful mess"), SearchHit("https://other.reliable.example/r", "t", "Awful night")]
    )
    ai = FakeAI({"rating": "negative", "quote": "A dreadful mess"})
    tm = {"_embedded": {"events": [FIXTURE["_embedded"]["events"][0]]}}
    # Both hits share one registered domain in the fixture, so give the second its own.
    sites._entries["second.example"] = {"domain": "second.example", "automated_access": "allowed", "reliable": True}
    tavily.hits[1] = SearchHit("https://second.example/r", "t", "Awful night")
    run_pipeline(conn, deps(sites, tavily=tavily, ai=ai, ticketmaster=tm), NOW)
    assert db.eligible_events(conn, NOW) == []


def test_pasted_captions_cannot_meet_the_two_source_rule(conn, sites):
    tm = {"_embedded": {"events": [FIXTURE["_embedded"]["events"][0]]}}
    event = make_event(url="https://reliable.example/p", source_type="pasted")
    db.save_paste(conn, "https://www.tiktok.com/@a/1", "Truly a dreadful mess", event)
    db.save_paste(conn, "https://www.instagram.com/p/2", "Truly a dreadful mess", event)
    ai = FakeAI({"rating": "negative", "quote": "Truly a dreadful mess"})
    d = deps(sites, {"https://reliable.example/p": "<p>Show on Sat, Nov 7 at Beacon Theatre</p>"}, tm, ai=ai)
    run_pipeline(conn, d, NOW)
    assert ai.calls == 0
    assert len(db.eligible_events(conn, NOW)) == 1


def test_same_post_pasted_twice_counts_once(conn, sites):
    tm = {"_embedded": {"events": [FIXTURE["_embedded"]["events"][0]]}}
    event = make_event(url="https://reliable.example/p", source_type="pasted")
    for _ in range(3):
        db.save_paste(conn, "https://www.tiktok.com/@a/1", "loved it", event)
    d = deps(sites, {"https://reliable.example/p": "<p>Show on Sat, Nov 7 at Beacon Theatre</p>"}, tm)
    run_pipeline(conn, d, NOW)
    assert db.eligible_events(conn, NOW)[0].score.total == 55


def test_parks_feed_event_is_eligible_without_opening_its_page(conn, sites):
    parks = [{"title": "Fall Foliage Walk", "link": {"url": "http://www.nycgovparks.org/events/walk"},
              "parknames": "Central Park", "starttime": "2026-11-08T11:00:00.000", "categories": "Nature | Tours"}]
    d = deps(sites, ticketmaster=EMPTY_TM, parks=parks)
    run_pipeline(conn, d, NOW)
    saved = db.eligible_events(conn, NOW)
    assert [(e.event.name, e.score.total) for e in saved] == [("Fall Foliage Walk", 20)]


def test_engage_feed_event_is_eligible_as_official_listing(conn, sites):
    engage = b"""<rss><channel><item><title>Southeast Asian Festival</title>
      <link>https://engage.nyu.edu/event/1002</link>
      <start xmlns="events">Sun, 08 Nov 2026 23:00:00 GMT</start>
      <location xmlns="events">GC Grand Hall</location><status xmlns="events">confirmed</status>
    </item></channel></rss>"""
    run_pipeline(conn, deps(sites, ticketmaster=EMPTY_TM, engage=engage), NOW)
    saved = db.eligible_events(conn, NOW)
    assert [(e.event.name, e.event.source, e.score.total) for e in saved] == [("Southeast Asian Festival", "nyu_engage", 20)]
