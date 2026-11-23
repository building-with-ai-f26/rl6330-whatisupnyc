import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from app import db
from app.models import Score
from app.pages import clean_image_url, page_image
from app.sources import nps, ticketmaster, wp_events
from tests.conftest import NY, make_event

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 2, 12, tzinfo=NY)


def test_page_image_reads_og_image_then_twitter_image():
    og = '<meta property="og:image" content="https://cdn.example/a.jpg"><meta name="twitter:image" content="https://cdn.example/b.jpg">'
    assert page_image(og, "https://venue.example/e/1") == "https://cdn.example/a.jpg"
    tw = '<meta name="twitter:image" content="https://cdn.example/b.jpg">'
    assert page_image(tw, "https://venue.example/e/1") == "https://cdn.example/b.jpg"


def test_page_image_makes_relative_urls_absolute():
    html = '<meta property="og:image" content="/img/poster.jpg">'
    assert page_image(html, "https://venue.example/e/1") == "https://venue.example/img/poster.jpg"


def test_page_image_keeps_only_https():
    for content in ["http://cdn.example/a.jpg", "javascript:alert(1)", "data:image/png;base64,AAAA", "", "   "]:
        html = f'<meta property="og:image" content="{content}">'
        assert page_image(html, "https://venue.example/e/1") is None, content
    assert page_image('<meta property="og:image" content="/a.jpg">', "http://plain.example/e") is None


def test_page_image_missing_is_none():
    assert page_image("<html><head><title>x</title></head></html>", "https://venue.example/") is None
    assert page_image("", "https://venue.example/") is None


def test_clean_image_url():
    assert clean_image_url("https://cdn.example/a.jpg") == "https://cdn.example/a.jpg"
    assert clean_image_url("/common/a.jpg", "https://www.nps.gov") == "https://www.nps.gov/common/a.jpg"
    assert clean_image_url(None) is None
    assert clean_image_url(42) is None
    assert clean_image_url("http://cdn.example/a.jpg") is None


def test_ticketmaster_picks_a_wide_image():
    record = copy.deepcopy(json.loads((FIXTURES / "ticketmaster.json").read_text())["_embedded"]["events"][0])
    record["images"] = [
        {"ratio": "4_3", "url": "https://s1.ticketm.net/small.jpg", "width": 305},
        {"ratio": "16_9", "url": "https://s1.ticketm.net/wide-small.jpg", "width": 205},
        {"ratio": "16_9", "url": "https://s1.ticketm.net/wide.jpg", "width": 1024},
        {"ratio": "16_9", "url": "https://s1.ticketm.net/huge.jpg", "width": 2048},
    ]
    assert ticketmaster._to_event(record).image_url == "https://s1.ticketm.net/wide.jpg"
    record["images"] = [{"ratio": "4_3", "url": "https://s1.ticketm.net/only.jpg", "width": 305}]
    assert ticketmaster._to_event(record).image_url == "https://s1.ticketm.net/only.jpg"
    record.pop("images")
    assert ticketmaster._to_event(record).image_url is None


def test_wordpress_calendar_image():
    record = copy.deepcopy(json.loads((FIXTURES / "wp_events.json").read_text())["events"][0])
    record["image"] = {"url": "https://movingimage.org/wp-content/uploads/poster.jpg", "width": 1200}
    assert wp_events._to_event(record, "movingimage.org", {"organization": "Museum of the Moving Image"}).image_url == "https://movingimage.org/wp-content/uploads/poster.jpg"
    record["image"] = False  # what the feed sends when there is none
    assert wp_events._to_event(record, "movingimage.org", {"organization": "Museum of the Moving Image"}).image_url is None


def test_nps_image_made_absolute():
    body = copy.deepcopy(json.loads((FIXTURES / "nps.json").read_text()))
    for record in body["data"]:
        record["images"] = [{"url": "/common/uploads/event_calendar/walk.jpg", "altText": "Walk"}]
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    events = nps.fetch_nps_events(client, "k", NOW)
    assert events and all(e.image_url == "https://www.nps.gov/common/uploads/event_calendar/walk.jpg" for e in events)


def test_image_is_saved_and_read_back(conn):
    event_id = db.save_event(conn, make_event(image_url="https://cdn.example/a.jpg"), Score(40, True))
    (saved,) = db.eligible_events(conn, NOW)
    assert saved.id == event_id and saved.event.image_url == "https://cdn.example/a.jpg"


def test_saving_again_without_an_image_keeps_the_old_one(conn):
    db.save_event(conn, make_event(image_url="https://cdn.example/a.jpg"), Score(40, True))
    db.save_event(conn, make_event(), Score(40, True))
    assert db.eligible_events(conn, NOW)[0].event.image_url == "https://cdn.example/a.jpg"


def test_fill_event_image_only_when_missing(conn):
    from app.checks import dedupe_key

    plain, pictured = make_event(name="Plain"), make_event(name="Pictured", url="https://reliable.example/e/2")
    db.save_event(conn, plain, Score(40, True))
    db.save_event(conn, pictured, Score(40, True))
    db.save_event(conn, make_event(name="Pictured", url="https://reliable.example/e/2", image_url="https://cdn.example/old.jpg"), Score(40, True))
    db.fill_event_image(conn, dedupe_key(plain), "https://cdn.example/new.jpg")
    db.fill_event_image(conn, dedupe_key(pictured), "https://cdn.example/new.jpg")
    by_name = {e.event.name: e.event.image_url for e in db.eligible_events(conn, NOW - timedelta(days=1))}
    assert by_name == {"Plain": "https://cdn.example/new.jpg", "Pictured": "https://cdn.example/old.jpg"}
