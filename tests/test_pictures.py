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
    # Reliable but not opened by us, so the feed event is still saved without fetching its page.
    sites._entries["nyc.gov"] = {"domain": "nyc.gov", "automated_access": "prohibited", "reliable": True}
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


# Muse and a feed listing the same event: both stay (readers' saves and links keep working), but a list
# never shows both.
def copies(conn, sites):
    muse = _intake("Macy's Thanksgiving Day Parade Balloon Inflation", START, image="https://cdn.example/m.jpg",
                   venue="W 72nd Street & Columbus Avenue")
    db.save_intake(conn, [("https://www.instagram.com/p/M/", muse, "")], NOW)
    run_with(conn, sites, FakeTavily(), feed_event())


def test_both_copies_stay_listed(conn, sites):
    copies(conn, sites)
    assert len(pictures(conn)) == 2


def test_a_list_never_shows_both_copies(conn, sites):
    from markupsafe import escape

    from tests.fakes import ChoosingAI
    from tests.test_main import client_for, open_list, signed_in_ana

    copies(conn, sites)
    client = client_for(conn, sites, ChoosingAI(how_many=5))
    signed_in_ana(client)
    html = open_list(client).text
    cards = sum(html.count(f'data-name="{escape(name)}"><button class="plain toggle"') for name in pictures(conn))
    assert cards == 1


def test_more_like_these_never_adds_a_copy_of_a_listed_event(conn, sites):
    from app import saved_lists
    from tests.fakes import ChoosingAI
    from tests.test_main import client_for, ana_id, signed_in_ana

    copies(conn, sites)
    client = client_for(conn, sites)
    signed_in_ana(client)
    reader = db.get_profile(conn, ana_id(client))
    feed = next(e for e in db.eligible_events(conn, NOW) if e.event.source == "nyc_calendar")
    first = saved_lists.make_list(conn, ChoosingAI(how_many=1), reader, NOW)
    assert [p.event.id for p in first] in ([feed.id], [next(e.id for e in db.eligible_events(conn, NOW) if e.id != feed.id)])
    after = saved_lists.make_list(conn, ChoosingAI(how_many=5), reader, NOW, more_like_ids=[first[0].event.id])
    names = [p.event.event.name for p in after]
    assert sum("Balloon Inflation" in n for n in names) == 1


def test_similar_but_different_events_are_both_allowed_on_a_list(conn, sites):
    from app.pictures import is_copy
    rogue = _intake("Rogue Black Friday Blowout Sale", START, venue="Rogue Flagship")
    shop = feed_event(name="Black Friday Sale At The Shop", venue="Scandinavia House")
    yoga = feed_event(name="Free Yoga Class", venue="Flushing Meadows")
    sunset = _intake("Free Sunset Yoga Class at Domino Park", START, venue="Domino Park")
    assert not is_copy(rogue, shop) and not is_copy(yoga, sunset)


# Tighter picture rules.
def test_short_generic_names_are_not_searched(conn, sites):
    tavily = FakeTavily(hits=[NEWS], images=[PHOTO])
    new_event_run(conn, sites, tavily, feed_event(name="Comedy Night"))
    assert tavily.image_queries == []


def test_a_picture_from_a_site_that_did_not_write_about_it_is_not_used(conn, sites):
    elsewhere = ImageHit("https://listing.example/img/b.jpg", "Macy's parade balloon inflation at night")
    new_event_run(conn, sites, FakeTavily(hits=[NEWS], images=[elsewhere]), feed_event())
    assert pictures(conn)[BALLOONS] is None
