import hashlib
from datetime import timedelta

from app import db
from app.limits import LIMITS
from tests.test_main import NOW, client_for

TOKEN = "test-intake-token-0123456789abcdef"
HASH = hashlib.sha256(TOKEN.encode()).hexdigest()
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def event(n=1, **changes):
    fields = {
        "name": f"Show {n}",
        "start": (NOW + timedelta(days=3)).isoformat(),
        "venue": "Beacon Theatre",
        "official_url": f"https://reliable.example/e/{n}",
        "post_url": f"https://www.instagram.com/p/POST{n}/",
        "caption": "Great show this Friday",
    }
    fields.update(changes)
    return fields


def send(client, events, headers=AUTH):
    client.headers.pop("Origin", None)  # Muse is not a browser on our site
    return client.post("/api/intake", json={"collected_at": NOW.isoformat(), "events": events}, headers=headers)


def test_intake_is_off_without_a_configured_token(conn, sites):
    assert send(client_for(conn, sites), [event()]).status_code == 404


def test_wrong_or_missing_token_is_refused(conn, sites):
    client = client_for(conn, sites, intake_hash=HASH)
    assert send(client, [event()], headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert send(client, [event()], headers={}).status_code == 401
    assert db.waiting_intake(conn) == []


def test_good_events_are_stored_for_the_morning_check(conn, sites):
    client = client_for(conn, sites, intake_hash=HASH)
    response = send(client, [event(1), event(2)])
    assert response.status_code == 200
    assert response.json() == {"accepted": 2, "updated": 0, "rejected": []}
    stored = db.waiting_intake(conn)
    assert [e.name for _, e in stored] == ["Show 1", "Show 2"]
    first = stored[0][1]
    assert first.url == "https://reliable.example/e/1" and first.source == "intake"
    assert first.source_type == "api"  # a trusted feed: its page is never fetched


def test_end_date_and_picture_are_stored(conn, sites):
    client = client_for(conn, sites, intake_hash=HASH)
    end = NOW + timedelta(days=20)
    item = event(1, end=end.isoformat(), image_url="https://cdn.venue.example/poster.jpg")
    assert send(client, [item]).json()["accepted"] == 1
    (_, stored), = db.waiting_intake(conn)
    assert stored.end == end and stored.image_url == "https://cdn.venue.example/poster.jpg"


def test_an_event_that_started_but_is_still_running_is_accepted(conn, sites):
    client = client_for(conn, sites, intake_hash=HASH)
    item = event(1, start=(NOW - timedelta(days=30)).isoformat(), end=(NOW + timedelta(days=60)).isoformat())
    assert send(client, [item]).json() == {"accepted": 1, "updated": 0, "rejected": []}


def test_sending_the_same_post_again_updates_it(conn, sites):
    client = client_for(conn, sites, intake_hash=HASH)
    send(client, [event(1)])
    db.mark_intake_checked(conn, [i for i, _ in db.waiting_intake(conn)], NOW)
    end = NOW + timedelta(days=9)
    body = send(client, [event(1, end=end.isoformat(), image_url="https://cdn.venue.example/p.jpg")]).json()
    assert body == {"accepted": 0, "updated": 1, "rejected": []}
    (_, stored), = db.waiting_intake(conn)  # waiting again, with the new details
    assert stored.end == end and stored.image_url == "https://cdn.venue.example/p.jpg"


def test_bad_events_are_rejected_with_reasons(conn, sites):
    client = client_for(conn, sites, intake_hash=HASH)
    bad = [
        event(1, name=""),
        event(2, start=(NOW - timedelta(days=1)).isoformat()),
        event(3, start="2026-11-27T20:00:00"),  # no time zone offset
        event(4, start=(NOW + timedelta(days=90)).isoformat()),
        event(5, official_url="http://venue.example/e"),
        event(6, official_url="https://www.instagram.com/p/X/"),
        event(7, venue=""),
        event(8, post_url=""),
        "not an object",
        event(10, official_url=None),  # readers need a page to go to
        event(11, end=(NOW + timedelta(days=1)).isoformat()),  # ends before it starts
        event(12, end="2026-11-27T20:00:00"),  # no time zone offset
        event(13, end=(NOW + timedelta(days=200)).isoformat()),  # runs longer than 180 days
        event(14, start=(NOW - timedelta(days=5)).isoformat(), end=(NOW - timedelta(days=1)).isoformat()),  # over
        event(15, image_url="http://cdn.venue.example/p.jpg"),
        event(16, image_url="https://scontent-lga3-1.cdninstagram.com/v/p.jpg"),
        event(17, image_url="https://scontent.xx.fbcdn.net/p.jpg"),
        event(18, image_url="not a link"),
        event(19, official_url="https://x.com/venue/status/1"),
        event(20, official_url="https://fb.me/abc"),
        event(21, official_url="https:///no-host"),
    ]
    body = send(client, bad + [event(9)]).json()
    assert body["accepted"] == 1
    assert [r["index"] for r in body["rejected"]] == list(range(len(bad)))
    assert all(r["reason"] for r in body["rejected"])
    assert [e.name for _, e in db.waiting_intake(conn)] == ["Show 9"]


def test_malformed_bodies_are_400(conn, sites):
    client = client_for(conn, sites, intake_hash=HASH)
    client.headers.pop("Origin", None)
    assert client.post("/api/intake", content=b"not json", headers={**AUTH, "Content-Type": "application/json"}).status_code == 400
    assert client.post("/api/intake", json={"events": "nope"}, headers=AUTH).status_code == 400


def test_at_most_200_events_a_day(conn, sites):
    client = client_for(conn, sites, intake_hash=HASH)
    assert send(client, [event(n) for n in range(150)]).json()["accepted"] == 150
    body = send(client, [event(n) for n in range(150, 260)]).json()
    assert body["accepted"] == 50
    assert sum(1 for r in body["rejected"] if "200" in r["reason"]) == 60
    assert send(client, [event(999)]).status_code == 429


def test_sending_a_post_again_counts_toward_the_daily_limit(conn, sites):
    client = client_for(conn, sites, intake_hash=HASH)
    send(client, [event(n) for n in range(200)])
    with conn.connection() as c:  # yesterday's posts
        c.execute("update intake_events set received_day = received_day - 1")
    assert send(client, [event(n) for n in range(200)]).json()["updated"] == 200
    assert send(client, [event(999)]).status_code == 429


def test_too_large_a_body_is_refused(conn, sites):
    client = client_for(conn, sites, intake_hash=HASH)
    huge = [event(n, caption="x" * 5000) for n in range(200)]
    assert send(client, huge).status_code == 413
    assert db.waiting_intake(conn) == []


def test_intake_has_its_own_per_address_limit(conn, sites):
    client = client_for(conn, sites, intake_hash=HASH)
    for _ in range(LIMITS["intake"][0]):
        send(client, [])
    assert send(client, []).status_code == 429
