from datetime import timedelta

from app.models import Pick, Score, ScoredEvent
from app.newsletter import card, fallback_picture, render_newsletter
from tests.conftest import make_event
from tests.test_main import NOW, client_for


def scored(**changes):
    return ScoredEvent(5, make_event(start=NOW + timedelta(days=2), **changes), Score(40, True))


def test_each_kind_of_event_gets_a_matching_picture():
    assert fallback_picture(make_event(category="Music")) == "/static/pictures/music.png"
    assert fallback_picture(make_event(category="Sports")) == "/static/pictures/sports.png"
    assert fallback_picture(make_event(category="Arts & Theatre")) == "/static/pictures/arts.png"
    assert fallback_picture(make_event(name="Late show", category="Comedy")) == "/static/pictures/comedy.png"
    assert fallback_picture(make_event(name="Fall foliage walk", category="Nature, Free")) == "/static/pictures/outdoors.png"
    assert fallback_picture(make_event(name="Stand-up night", category=None)) == "/static/pictures/comedy.png"
    assert fallback_picture(make_event(name="Something", category=None)) == "/static/pictures/city.png"


def test_cards_without_a_picture_get_the_matching_one():
    assert card(scored(category="Music"))["image"] == "/static/pictures/music.png"
    assert card(scored(image_url="https://cdn.example/a.jpg"))["image"] == "https://cdn.example/a.jpg"


def test_email_uses_the_full_address_of_the_matching_picture():
    html = render_newsletter([Pick(scored(category="Music"), "fits")], now=NOW, site_url="https://whatisupnyc.com")
    assert '<img src="https://whatisupnyc.com/static/pictures/music.png" width="72" height="72"' in html


def test_every_picture_is_served(conn, sites):
    client = client_for(conn, sites)
    for kind in ("music", "sports", "arts", "comedy", "outdoors", "city"):
        response = client.get(f"/static/pictures/{kind}.png")
        assert response.status_code == 200 and response.headers["content-type"] == "image/png", kind


def test_share_page_previews_with_the_matching_picture(conn, sites):
    from app import db

    event_id = db.save_event(conn, make_event(category="Music", start=NOW + timedelta(days=2)), Score(40, True))
    html = client_for(conn, sites).get(f"/e/{event_id}").text
    assert '<meta property="og:image" content="https://testserver/static/pictures/music.png">' in html
    assert '<img src="/static/pictures/music.png"' in html
