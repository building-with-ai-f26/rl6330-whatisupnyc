from datetime import datetime, timedelta

import httpx

from app import db
from app.models import Score
from scripts.backfill_images import backfill_images
from tests.conftest import NY, make_event

NOW = datetime(2026, 11, 2, 12, tzinfo=NY)


def page_server(pages):
    def handle(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        html = pages.get(str(request.url))
        return httpx.Response(200, text=html) if html else httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handle))


def test_fills_pictures_from_feeds_and_event_pages(conn, sites):
    fed = make_event(name="Fed", url="https://reliable.example/fed")
    paged = make_event(name="Paged", url="https://reliable.example/paged")
    bare = make_event(name="Bare", url="https://reliable.example/bare")
    for event in (fed, paged, bare):
        db.save_event(conn, event, Score(40, True))
    feed = [make_event(name="Fed", url="https://reliable.example/fed", image_url="https://cdn.example/fed.jpg")]
    http = page_server({"https://reliable.example/paged": '<meta property="og:image" content="/p.jpg">'})
    counts = backfill_images(conn, http, sites, NOW, feed_events=feed)
    images = {e.event.name: e.event.image_url for e in db.eligible_events(conn, NOW)}
    assert images == {"Fed": "https://cdn.example/fed.jpg", "Paged": "https://reliable.example/p.jpg", "Bare": None}
    assert counts == {"from_feeds": 1, "pages_checked": 2, "from_pages": 1}


def test_keeps_existing_pictures_and_skips_past_and_blocked_events(conn, sites):
    db.save_event(conn, make_event(name="Has one", url="https://reliable.example/a", image_url="https://cdn.example/keep.jpg"), Score(40, True))
    db.save_event(conn, make_event(name="Past", url="https://reliable.example/past", start=NOW - timedelta(days=1)), Score(40, True))
    db.save_event(conn, make_event(name="Blocked", url="https://blocked.example/x"), Score(40, True))
    asked = []

    def handle(request):
        asked.append(str(request.url))
        return httpx.Response(200, text='<meta property="og:image" content="https://cdn.example/new.jpg">')

    counts = backfill_images(conn, httpx.Client(transport=httpx.MockTransport(handle)), sites, NOW, feed_events=[])
    assert db.eligible_events(conn, NOW)[0].event.image_url == "https://cdn.example/keep.jpg"
    assert not any("blocked.example" in url or "/past" in url or "/a" == url[-2:] for url in asked)
    assert counts["from_pages"] == 0
