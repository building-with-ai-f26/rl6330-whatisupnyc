import json
from datetime import datetime
from pathlib import Path

import httpx

from app import db
from app.ai import AIUnavailable
from app.pipeline import PipelineDeps, run_pipeline
from app.scoring import Entry, Reputable
from app.sites import Sites
from app.sources import wp_events
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


def deps(sites, pages=None, ticketmaster=FIXTURE, tavily=None, ai=None, parks=None, engage=None, wp=None, nps=None,
         calendar=None):
    def handler(request):
        if request.url.host == "app.ticketmaster.com":
            return httpx.Response(200, json=ticketmaster)
        if request.url.host == "api.nyc.gov":
            return httpx.Response(200, json=calendar or {"items": [], "pagination": {"numPages": 0}})
        if request.url.host == "developer.nps.gov":
            return httpx.Response(200, json=nps or {"total": "0", "data": []})
        if request.url.host == "data.cityofnewyork.us":
            return httpx.Response(200, json=parks or [])
        if str(request.url) == "https://engage.nyu.edu/events.rss" and engage:
            return httpx.Response(200, content=engage)
        if request.url.path == "/wp-json/tribe/events/v1/events" and wp:
            return httpx.Response(200, json=wp)
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
        nps_key="k",
        nyc_calendar_key="k",
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
    assert [(e.event.name, e.event.source, e.score.total) for e in saved] == [("Southeast Asian Festival", "nyu_engage", 10)]


def test_event_on_two_feeds_gets_overlap_points(conn, sites):
    parks = [{"title": "Fall Foliage Walk", "link": {"url": "http://www.nycgovparks.org/events/walk"},
              "parknames": "Central Park", "starttime": "2026-11-08T11:00:00.000", "categories": "Nature"}]
    engage = b"""<rss><channel><item><title>Fall Foliage Walk</title>
      <link>https://engage.nyu.edu/event/7</link>
      <start xmlns="events">Sun, 08 Nov 2026 16:00:00 GMT</start>
      <location xmlns="events">Central Park</location><status xmlns="events">confirmed</status>
    </item></channel></rss>"""
    run_pipeline(conn, deps(sites, ticketmaster=EMPTY_TM, parks=parks, engage=engage), NOW)
    saved = db.eligible_events(conn, NOW)
    # One merged event: NYC Parks listing (20) plus 10 for the second feed.
    assert [(e.event.name, e.score.total) for e in saved] == [("Fall Foliage Walk", 30)]


def test_wordpress_feed_event_is_saved_as_official_listing(conn, monkeypatch):
    monkeypatch.setattr(wp_events, "PAUSE_SECONDS", 0)
    feed_url = "https://movingimage.org/wp-json/tribe/events/v1/events"
    sites = Sites([{"domain": "movingimage.org", "automated_access": "allowed", "reliable": True,
                    "events_api": {"url": feed_url, "organization": "Museum of the Moving Image"}}])
    feed = {"events": [{"title": "Paths of Glory", "start_date": "2026-11-07 15:30:00", "timezone": "America/New_York",
                        "url": "https://movingimage.org/event/paths-of-glory/", "venue": [], "categories": []}]}
    page = {"https://movingimage.org/event/paths-of-glory/": "<p>Paths of Glory</p>"}
    run_pipeline(conn, deps(sites, page, EMPTY_TM, wp=feed), NOW)
    saved = db.eligible_events(conn, NOW)
    assert [(e.event.name, e.event.source, e.score.total) for e in saved] == [
        ("Paths of Glory", "wp:movingimage.org", 20)
    ]


def test_national_park_service_event_is_saved_as_official_listing(conn, sites):
    nps = {"total": "1", "data": [{"id": "A1", "title": "Fort Tompkins Tour", "location": "Fort Wadsworth Visitor Center",
                                    "parkfullname": "Gateway National Recreation Area", "isfree": "true",
                                    "types": ["Guided Tour"], "tags": [], "dates": ["2026-11-07"],
                                    "times": [{"timestart": "10:30 AM"}], "cancelleddatelist": []}]}
    run_pipeline(conn, deps(sites, ticketmaster=EMPTY_TM, nps=nps), NOW)
    saved = db.eligible_events(conn, NOW)
    assert [(e.event.name, e.event.source, e.score.total) for e in saved] == [("Fort Tompkins Tour", "nps", 20)]


def test_nyc_calendar_event_is_saved_as_official_listing(conn, sites):
    calendar = {"items": [{"startDate": "2026-11-07T13:00:00.000-05:00", "allDay": False, "canceled": False,
                           "permalink": "https://www1.nyc.gov/events/balloon-inflation/270016/1",
                           "name": "Macy's Parade Balloon Inflation", "location": "Museum of Natural History",
                           "boroughs": ["Mn"], "categories": "Free,Holidays,General Events"}],
                "pagination": {"currentPage": 1, "numPages": 1, "isLastPage": True}}
    run_pipeline(conn, deps(sites, ticketmaster=EMPTY_TM, calendar=calendar), NOW)
    saved = db.eligible_events(conn, NOW)
    assert [(e.event.name, e.event.source, e.score.total) for e in saved] == [
        ("Macy's Parade Balloon Inflation", "nyc_calendar", 20)
    ]


PASTE_DETAILS = {"name": "Show", "date": "2026-11-07", "time": "20:00", "venue": "Beacon Theatre", "city": None, "organizer": None}
PASTE_PAGE = {"https://reliable.example/show": "<p>Show on Sat, Nov 7 at Beacon Theatre</p>"}


def test_run_checks_waiting_pastes_then_clears_them(conn, sites):
    db.save_waiting_paste(conn, "https://www.tiktok.com/@a/1", "Show at Beacon, Nov 7")
    tavily = FakeTavily([SearchHit("https://reliable.example/show", "t", "c")])
    run_pipeline(conn, deps(sites, PASTE_PAGE, EMPTY_TM, tavily=tavily, ai=FakeAI(dict(PASTE_DETAILS))), NOW)
    assert db.list_waiting_pastes(conn) == []
    (link, _, event), = db.list_pastes(conn)
    assert (link, event.url) == ("https://www.tiktok.com/@a/1", "https://reliable.example/show")


def test_unconfirmed_waiting_paste_is_dropped(conn, sites):
    db.save_waiting_paste(conn, "https://www.tiktok.com/@a/1", "Show at Beacon, Nov 7")
    run_pipeline(conn, deps(sites, ticketmaster=EMPTY_TM, ai=FakeAI(dict(PASTE_DETAILS))), NOW)
    assert db.list_waiting_pastes(conn) == [] and db.list_pastes(conn) == []


def test_waiting_paste_stays_when_the_ai_is_busy(conn, sites):
    db.save_waiting_paste(conn, "https://www.tiktok.com/@a/1", "Show at Beacon, Nov 7")
    run_pipeline(conn, deps(sites, ticketmaster=EMPTY_TM, ai=FakeAI(error=AIUnavailable("busy"))), NOW)
    assert len(db.list_waiting_pastes(conn)) == 1


def test_run_checks_at_most_ten_waiting_pastes(conn, sites):
    for n in range(12):
        db.save_waiting_paste(conn, f"https://www.tiktok.com/@a/{n}", "Show at Beacon, Nov 7")
    ai = FakeAI(*[dict(PASTE_DETAILS)] * 12)
    run_pipeline(conn, deps(sites, ticketmaster=EMPTY_TM, ai=ai), NOW)
    assert len(db.list_waiting_pastes(conn)) == 2


def test_waiting_paste_is_dropped_after_three_busy_runs(conn, sites):
    db.save_waiting_paste(conn, "https://www.tiktok.com/@a/1", "Show at Beacon, Nov 7")
    for _ in range(3):
        run_pipeline(conn, deps(sites, ticketmaster=EMPTY_TM, ai=FakeAI(error=AIUnavailable("busy"))), NOW)
    assert db.list_waiting_pastes(conn) == []


def test_new_event_takes_its_pages_preview_image(conn, sites):
    tavily = FakeTavily([SearchHit("https://reliable.example/good", "t", "c")])
    html = '<meta property="og:image" content="/posters/good.jpg">' + jsonld("Good Show")
    run_pipeline(conn, deps(sites, {"https://reliable.example/good": html}, EMPTY_TM, tavily), NOW)
    (event,) = db.eligible_events(conn, NOW)
    assert event.event.image_url == "https://reliable.example/posters/good.jpg"


def test_saved_event_seen_again_picks_up_a_feed_image(conn, sites):
    run_pipeline(conn, deps(sites), NOW)
    assert db.eligible_events(conn, NOW)[0].event.image_url is None
    pictured = json.loads(json.dumps(FIXTURE))
    for record in pictured["_embedded"]["events"]:
        record["images"] = [{"ratio": "16_9", "url": "https://s1.ticketm.net/wide.jpg", "width": 1024}]
    run_pipeline(conn, deps(sites, ticketmaster=pictured), NOW)
    assert db.eligible_events(conn, NOW)[0].event.image_url == "https://s1.ticketm.net/wide.jpg"


def _intake(name, start, end=None, image=None, venue="Grand Hall"):
    from app.models import CandidateEvent

    return CandidateEvent(name=name, start=start, end=end, venue=venue, city="New York",
                          url=f"https://blocked.example/{name.replace(' ', '-')}", source="intake",
                          source_type="api", image_url=image)


def test_muse_events_are_saved_without_fetching_their_page(conn, sites):
    from datetime import timedelta

    running = _intake("Winter Market", NOW - timedelta(days=20), NOW + timedelta(days=60), "https://cdn.example/m.jpg")
    soon = _intake("Sample Sale", NOW + timedelta(days=2))
    over = _intake("Old Fair", NOW - timedelta(days=9), NOW - timedelta(days=1))
    db.save_intake(conn, [(f"https://www.instagram.com/p/{e.name[:3]}/", e, "") for e in (running, soon, over)], NOW)
    d = deps(sites, ticketmaster=EMPTY_TM)
    fetched = []
    d.http.event_hooks["request"].append(lambda request: fetched.append(str(request.url)))
    run_pipeline(conn, d, NOW)
    assert not [url for url in fetched if "blocked.example" in url]
    listed = {e.event.name: e for e in db.eligible_events(conn, NOW)}
    assert set(listed) == {"Winter Market", "Sample Sale"}
    market = listed["Winter Market"].event
    assert market.end == running.end and market.image_url == "https://cdn.example/m.jpg"
    assert db.dropped_reasons(conn) == {"event has ended": 1}
    assert db.waiting_intake(conn) == []


def test_a_muse_event_already_found_elsewhere_keeps_its_score_and_gains_details(conn, sites):
    from datetime import timedelta

    from app.models import Score

    start = NOW + timedelta(days=3)
    db.save_event(conn, make_event(name="Big Show", start=start, venue="Grand Hall"), Score(60, True))
    copy = _intake("Big Show", start, start + timedelta(days=2), "https://cdn.example/b.jpg")
    db.save_intake(conn, [("https://www.instagram.com/p/BIG/", copy, "")], NOW)
    run_pipeline(conn, deps(sites, ticketmaster=EMPTY_TM), NOW)
    (saved,) = [e for e in db.eligible_events(conn, NOW) if e.event.name == "Big Show"]
    assert saved.score.total == 60 and saved.event.source == "test"
    assert saved.event.image_url == "https://cdn.example/b.jpg" and saved.event.end == copy.end
