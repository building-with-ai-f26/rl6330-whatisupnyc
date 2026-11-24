"""Pictures for well-known events from an image search, and one card for an event Muse and a feed both list."""
from datetime import timedelta

from app import db
from app.models import Score
from app.pipeline import MAX_PICTURE_SEARCHES, run_pipeline
from app.sources.web import ImageHit, SearchHit
from tests.conftest import make_event
from tests.fakes import FakeTavily
from tests.test_pipeline import EMPTY_TM, NOW, _intake, deps

START = NOW + timedelta(days=3)
BALLOONS = "Macy's Parade Balloon Inflation"
NEWS = SearchHit("https://reliable.example/news/balloons", "Balloons", f"See {BALLOONS} by the museum this week.")
PHOTO = ImageHit("https://reliable.example/img/balloon.jpg", "Workers inflating a Macy's parade balloon by the museum")


def feed_event(name=BALLOONS, **changes):
    fields = dict(name=name, start=START, venue="Museum of Natural History", url="https://www1.nyc.gov/events/1",
                  source="nyc_calendar", source_type="api")
    fields.update(changes)
    return make_event(**fields)


def pictures(conn):
    return {e.event.name: e.event.image_url for e in db.eligible_events(conn, NOW)}


def run_with(conn, sites, tavily, *events):
    for event in events:
        db.save_event(conn, event, Score(20, True))
    run_pipeline(conn, deps(sites, ticketmaster=EMPTY_TM, tavily=tavily), NOW)


# Searching for a picture: once, for events saved in this run without one.
def new_event_run(conn, sites, tavily, event):
    calendar = {"items": [{"startDate": event.start.isoformat(), "allDay": False, "canceled": False,
                           "permalink": event.url, "name": event.name, "location": event.venue,
                           "boroughs": ["Mn"], "categories": "Free,Holidays"}],
                "pagination": {"currentPage": 1, "numPages": 1, "isLastPage": True}}
    run_pipeline(conn, deps(sites, ticketmaster=EMPTY_TM, tavily=tavily, calendar=calendar), NOW)


def test_a_well_known_event_gets_a_picture_from_a_trusted_site(conn, sites):
    tavily = FakeTavily(hits=[NEWS], images=[PHOTO])
    new_event_run(conn, sites, tavily, feed_event())
    assert pictures(conn)[BALLOONS] == PHOTO.url
    assert tavily.image_queries == [f'"{BALLOONS}" New York']


def test_no_picture_when_no_other_trusted_site_mentions_the_event(conn, sites):
    # Only the event's own listing mentions it, so it isn't known enough to trust a search picture.
    tavily = FakeTavily(hits=[SearchHit("https://reliable.example/other", "x", "Something else")], images=[PHOTO])
    new_event_run(conn, sites, tavily, feed_event())
    assert pictures(conn)[BALLOONS] is None


def test_mentions_on_the_events_own_site_do_not_count(conn, sites):
    # The event's own listing is on www1.nyc.gov, so a mention elsewhere on nyc.gov proves nothing.
    sites._entries.append({"domain": "nyc.gov", "automated_access": "allowed", "reliable": True}) \
        if hasattr(sites, "_entries") else None
    tavily = FakeTavily(hits=[SearchHit("https://www.nyc.gov/listing", "x", f"{BALLOONS} details")], images=[PHOTO])
    new_event_run(conn, sites, tavily, feed_event())
    assert pictures(conn)[BALLOONS] is None


def test_a_picture_that_does_not_show_the_event_is_not_used(conn, sites):
    bike = ImageHit("https://reliable.example/img/bike.jpg", "A delivery worker rides a green bicycle")
    new_event_run(conn, sites, FakeTavily(hits=[NEWS], images=[bike]), feed_event())
    assert pictures(conn)[BALLOONS] is None


def test_the_first_matching_picture_is_used_and_social_apps_are_skipped(conn, sites):
    social = ImageHit("https://scontent.cdninstagram.com/b.jpg", "Macy's parade balloon inflation crowd")
    plain = ImageHit("http://reliable.example/img/b.jpg", "Macy's parade balloon inflation at night")
    off = ImageHit("https://reliable.example/img/x.jpg", "A city street")
    new_event_run(conn, sites, FakeTavily(hits=[NEWS], images=[off, social, plain, PHOTO]), feed_event())
    assert pictures(conn)[BALLOONS] == PHOTO.url


def test_events_with_a_picture_are_not_searched(conn, sites):
    tavily = FakeTavily(hits=[NEWS], images=[PHOTO])
    new_event_run(conn, sites, tavily, feed_event(url="https://www1.nyc.gov/events/1"))
    tavily2 = FakeTavily(hits=[NEWS], images=[PHOTO])
    run_pipeline(conn, deps(sites, ticketmaster=EMPTY_TM, tavily=tavily2), NOW)  # already saved: not new
    assert tavily2.image_queries == []


def test_picture_searches_are_capped_each_run(conn, sites):
    tavily = FakeTavily(hits=[], images=[])
    items = [{"startDate": START.isoformat(), "allDay": False, "canceled": False,
              "permalink": f"https://www1.nyc.gov/events/{n}", "name": f"Festival Number {n} Parade",
              "location": f"Hall {n}", "boroughs": ["Mn"], "categories": "Free"} for n in range(MAX_PICTURE_SEARCHES + 5)]
    calendar = {"items": items, "pagination": {"currentPage": 1, "numPages": 1, "isLastPage": True}}
    run_pipeline(conn, deps(sites, ticketmaster=EMPTY_TM, tavily=tavily, calendar=calendar), NOW)
    assert len(tavily.image_queries) == MAX_PICTURE_SEARCHES


def test_a_new_muse_event_without_a_picture_is_searched_too(conn, sites):
    tavily = FakeTavily(hits=[NEWS], images=[PHOTO])
    db.save_intake(conn, [("https://www.instagram.com/p/BAL/", _intake(BALLOONS, START), "")], NOW)
    run_pipeline(conn, deps(sites, ticketmaster=EMPTY_TM, tavily=tavily), NOW)
    assert pictures(conn)[BALLOONS] == PHOTO.url


# One card when Muse and a feed list the same event under slightly different names.
def test_a_muse_copy_of_a_feed_event_is_merged_into_it(conn, sites):
    muse = _intake("Macy's Thanksgiving Day Parade Balloon Inflation", START, image="https://cdn.example/m.jpg",
                   venue="W 72nd Street & Columbus Avenue")
    db.save_intake(conn, [("https://www.instagram.com/p/M/", muse, "")], NOW)
    run_with(conn, sites, FakeTavily(), feed_event())
    assert pictures(conn) == {BALLOONS: "https://cdn.example/m.jpg"}


def test_a_feed_event_found_after_the_muse_copy_still_merges(conn, sites):
    muse = _intake("Macy's Thanksgiving Day Parade Balloon Inflation", START, venue="W 72nd Street")
    db.save_intake(conn, [("https://www.instagram.com/p/M/", muse, "")], NOW)
    run_pipeline(conn, deps(sites, ticketmaster=EMPTY_TM), NOW)
    new_event_run(conn, sites, FakeTavily(), feed_event())
    assert list(pictures(conn)) == [BALLOONS]


def test_similar_but_different_events_stay_apart(conn, sites):
    muse = _intake("Rogue Black Friday Blowout Sale", START, venue="Rogue Flagship")
    db.save_intake(conn, [("https://www.instagram.com/p/R/", muse, "")], NOW)
    run_with(conn, sites, FakeTavily(), feed_event(name="Black Friday Sale At The Shop", venue="Scandinavia House"))
    assert set(pictures(conn)) == {"Rogue Black Friday Blowout Sale", "Black Friday Sale At The Shop"}


def test_same_name_on_another_day_is_not_merged(conn, sites):
    muse = _intake("Macy's Thanksgiving Day Parade Balloon Inflation", START + timedelta(days=1))
    db.save_intake(conn, [("https://www.instagram.com/p/M/", muse, "")], NOW)
    run_with(conn, sites, FakeTavily(), feed_event())
    assert len(pictures(conn)) == 2
