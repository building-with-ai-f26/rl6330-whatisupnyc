from datetime import timedelta

from app import db
from app.ai import AIUnavailable
from app.main import AI_BUSY
from tests.test_main import NOW, ana_id, client_for, open_list, signed_in_ana
from tests.test_saved_lists import Clock, add_events
from tests.fakes import ChoosingAI


def ready(conn, sites, clock=None, how_many=2):
    """Ana, signed in, with today's list (Jazz Night and Comedy Cellar) made."""
    add_events(conn, "Jazz Night", "Comedy Cellar", "Museum Late", "Park Walk")
    with conn.connection() as c:
        c.execute("update events set image_url = 'https://cdn.example/jazz.jpg' where name = 'Jazz Night'")
    ai = ChoosingAI(how_many)
    client = client_for(conn, sites, ai, clock=clock)
    signed_in_ana(client)
    open_list(client)
    ids = {e.event.name: e.id for e in db.eligible_events(conn, NOW - timedelta(days=30))}
    return client, ai, ids


def test_dashboard_shows_cards_with_pictures_and_reasons(conn, sites):
    client, ai, ids = ready(conn, sites)
    html = client.get("/newsletter").text
    assert f'id="event-{ids["Jazz Night"]}"' in html
    assert 'src="https://cdn.example/jazz.jpg"' in html
    assert "Fits: Comedy Cellar" in html
    assert '<img src=""' not in html and 'src="None"' not in html  # no picture: a plain block instead


def test_dashboard_never_shows_scores(conn, sites):
    client, ai, ids = ready(conn, sites)
    html = client.get("/newsletter").text.lower()
    assert "score" not in html


def test_save_for_later_and_unsave(conn, sites):
    client, ai, ids = ready(conn, sites)
    jazz = ids["Jazz Night"]
    response = client.post(f"/events/{jazz}/save")
    assert (response.status_code, response.headers["location"]) == (303, f"/newsletter#event-{jazz}")
    assert db.saved_for_later_ids(conn, ana_id(client)) == [jazz]
    html = client.get("/newsletter").text
    saved_section = html[html.index('id="saved"'):]
    assert f'id="saved-{jazz}"' in saved_section
    client.post(f"/events/{jazz}/unsave")
    assert db.saved_for_later_ids(conn, ana_id(client)) == []


def test_only_events_on_the_readers_lists_can_be_saved_or_rated(conn, sites):
    client, ai, ids = ready(conn, sites)
    never_listed = ids["Park Walk"]
    assert client.post(f"/events/{never_listed}/save").status_code == 404
    assert client.post(f"/events/{never_listed}/rate", data={"rating": "4"}).status_code == 404
    assert client.post("/events/999999/save").status_code == 404
    assert db.saved_for_later_ids(conn, ana_id(client)) == [] and db.ratings(conn, ana_id(client)) == {}


def test_rating_an_event_you_went_to(conn, sites):
    clock = Clock()
    client, ai, ids = ready(conn, sites, clock)
    jazz = ids["Jazz Night"]
    assert client.post(f"/events/{jazz}/rate", data={"rating": "4"}).status_code == 400  # hasn't happened yet
    clock.now = NOW + timedelta(days=4)  # the events were 3 days out
    html = open_list(client).text
    rate_section = html[html.index('id="rate"'):]
    assert f'id="rate-{jazz}"' in rate_section
    for bad in ("0", "6", "five", ""):
        assert client.post(f"/events/{jazz}/rate", data={"rating": bad}).status_code == 400
    assert client.post(f"/events/{jazz}/rate", data={"rating": "5"}).status_code == 303
    assert db.ratings(conn, ana_id(client)) == {jazz: 5}
    html = client.get("/newsletter").text
    assert 'aria-label="Rate 5 out of 5" aria-pressed="true"' in html


def test_more_like_these_form_covers_every_card(conn, sites):
    client, ai, ids = ready(conn, sites)
    html = client.get("/newsletter").text
    assert '<form id="more" method="post" action="/newsletter/more"' in html
    for name in ("Jazz Night", "Comedy Cellar"):
        assert f'name="event_ids" value="{ids[name]}" form="more"' in html


def test_share_buttons_point_at_the_public_event_page(conn, sites):
    client, ai, ids = ready(conn, sites)
    html = client.get("/newsletter").text
    assert f'data-share-url="https://testserver/e/{ids["Jazz Night"]}"' in html
    assert f'href="/e/{ids["Jazz Night"]}"' in html  # works without JavaScript too


def test_busy_ai_still_shows_the_last_list_as_cards(conn, sites):
    clock = Clock()
    client, ai, ids = ready(conn, sites, clock)
    clock.now += timedelta(days=1)
    ai.error = AIUnavailable("busy")
    html = open_list(client).text
    assert AI_BUSY in html and f'id="event-{ids["Jazz Night"]}"' in html


def test_nothing_new_today_says_what_to_do(conn, sites):
    client = client_for(conn, sites, ChoosingAI())
    signed_in_ana(client)
    html = open_list(client).text
    assert "Nothing new to suggest today" in html and 'href="/preferences"' in html


def test_dashboard_links_to_the_email_version(conn, sites):
    client, ai, ids = ready(conn, sites)
    assert 'href="/newsletter/email"' in client.get("/newsletter").text


def test_event_actions_need_a_reader_and_this_site(conn, sites):
    client, ai, ids = ready(conn, sites)
    jazz = ids["Jazz Night"]
    assert client.post(f"/events/{jazz}/save", headers={"Origin": "https://evil.example"}).status_code == 403
    client.cookies.clear()
    response = client.post(f"/events/{jazz}/save")
    assert (response.status_code, response.headers["location"]) == (303, "/signin")


def test_saved_events_the_site_stopped_recommending_are_hidden(conn, sites):
    client, ai, ids = ready(conn, sites)
    jazz = ids["Jazz Night"]
    client.post(f"/events/{jazz}/save")
    with conn.connection() as c:
        c.execute("update events set eligible = false where id = %s", (jazz,))
    html = client.get("/newsletter").text
    assert f'id="saved-{jazz}"' not in html


def test_more_like_these_keeps_the_ticked_events_and_says_it_makes_a_new_list(conn, sites):
    client, ai, ids = ready(conn, sites)
    html = client.get("/newsletter").text
    assert "makes a new list" in html.lower()
    jazz = ids["Jazz Night"]
    client.post("/newsletter/more", data={"event_ids": [jazz]})
    assert db.saved_for_later_ids(conn, ana_id(client)) == [jazz]


def test_a_picture_that_fails_to_load_leaves_the_plain_block(conn, sites):
    client, ai, ids = ready(conn, sites)
    html = client.get("/newsletter").text
    img = html[html.index('src="https://cdn.example/jazz.jpg"') - 10:html.index('src="https://cdn.example/jazz.jpg"') + 200]
    assert 'onerror="this.remove()"' in img


def test_a_running_event_is_not_rated_until_it_ends(conn, sites):
    clock = Clock()
    client, ai, ids = ready(conn, sites, clock)
    jazz = ids["Jazz Night"]
    with conn.connection() as c:
        c.execute("update events set ends_at = start + interval '10 days' where id = %s", (jazz,))
    clock.now = NOW + timedelta(days=4)  # started, still running
    html = client.get("/newsletter").text
    assert f'id="rate-{jazz}"' not in html
    assert client.post(f"/events/{jazz}/rate", data={"rating": "5"}).status_code == 400


FETCH = {"X-Requested-With": "fetch"}


def test_buttons_on_the_page_answer_without_a_reload(conn, sites):
    clock = Clock()
    client, ai, ids = ready(conn, sites, clock)
    jazz = ids["Jazz Night"]
    assert client.post(f"/events/{jazz}/save", headers=FETCH).json() == {"saved": True}
    assert db.saved_for_later_ids(conn, ana_id(client)) == [jazz]
    assert client.post(f"/events/{jazz}/unsave", headers=FETCH).json() == {"saved": False}
    clock.now = NOW + timedelta(days=4)
    assert client.post(f"/events/{jazz}/rate", data={"rating": "4"}, headers=FETCH).json() == {"rating": 4}
    assert db.ratings(conn, ana_id(client)) == {jazz: 4}


def test_a_refused_button_still_says_why(conn, sites):
    client, ai, ids = ready(conn, sites)
    assert client.post(f"/events/{ids['Park Walk']}/save", headers=FETCH).status_code == 404


def test_more_like_these_from_the_page_says_where_the_new_list_is(conn, sites):
    client, ai, ids = ready(conn, sites)
    response = client.post("/newsletter/more", data={"event_ids": [ids["Jazz Night"]]}, headers=FETCH)
    assert response.json() == {"next": "/newsletter"}


def test_the_page_updates_buttons_in_place_and_says_when_the_list_is_opening(conn, sites):
    client, ai, ids = ready(conn, sites)
    html = client.get("/newsletter").text
    assert f'action="/events/{ids["Jazz Night"]}/save" data-inplace' in html
    assert "Opening your list" in html
