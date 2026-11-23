from datetime import datetime, timedelta

from app import db
from app.ai import AIUnavailable
from app.limits import LIMITS
from app.main import AI_BUSY
from app.models import Score
from tests.conftest import NY, make_event
from tests.fakes import ChoosingAI
from tests.test_main import GOOD, NOW, ana_id, client_for, sign_up_ana, signed_in_ana


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def add_events(conn, *names, start=None):
    for n, name in enumerate(names):
        when = start or NOW + timedelta(days=3, hours=n)
        db.save_event(conn, make_event(name=name, start=when, url=f"https://reliable.example/{name}"), Score(40 - n, True))


def setup(conn, sites, clock=None, how_many=2):
    add_events(conn, "Jazz Night", "Comedy Cellar", "Museum Late", "Park Walk")
    ai = ChoosingAI(how_many)
    client = client_for(conn, sites, ai, clock=clock)
    return client, ai


def test_reloading_the_same_day_makes_no_new_ai_call(conn, sites):
    client, ai = setup(conn, sites)
    sign_up_ana(client)
    first = client.get("/newsletter").text
    second = client.get("/newsletter").text
    assert ai.calls == 1
    assert "Jazz Night" in first and "Jazz Night" in second and "Fits: Comedy Cellar" in second


def test_a_new_new_york_day_makes_a_new_list(conn, sites):
    clock = Clock()
    client, ai = setup(conn, sites, clock)
    signed_in_ana(client)
    client.get("/newsletter")
    clock.now += timedelta(days=1)
    client.get("/newsletter")
    assert ai.calls == 2


def test_a_list_made_late_evening_counts_for_that_new_york_day(conn, sites):
    clock = Clock(datetime(2026, 11, 2, 23, 30, tzinfo=NY))  # 04:30 UTC on Nov 3
    client, ai = setup(conn, sites, clock)
    signed_in_ana(client)
    client.get("/newsletter")
    clock.now = datetime(2026, 11, 2, 23, 50, tzinfo=NY)
    client.get("/newsletter")
    assert ai.calls == 1
    clock.now = datetime(2026, 11, 3, 0, 10, tzinfo=NY)
    client.get("/newsletter")
    assert ai.calls == 2


def test_new_list_skips_events_on_earlier_lists(conn, sites):
    clock = Clock()
    client, ai = setup(conn, sites, clock)
    signed_in_ana(client)
    client.get("/newsletter")
    clock.now += timedelta(days=1)
    html = client.get("/newsletter").text
    assert ai.offered[1] == ["Museum Late", "Park Walk"]
    assert "Museum Late" in html and "Jazz Night" not in html


def test_saved_list_skips_events_that_no_longer_exist(conn, sites):
    client, ai = setup(conn, sites)
    signed_in_ana(client)
    client.get("/newsletter")
    with conn.connection() as c:
        c.execute("delete from events where name = 'Jazz Night'")
    html = client.get("/newsletter").text
    assert "Comedy Cellar" in html and "Jazz Night" not in html and ai.calls == 1


def test_ai_busy_shows_the_previous_list_with_the_message(conn, sites):
    clock = Clock()
    client, ai = setup(conn, sites, clock)
    signed_in_ana(client)
    client.get("/newsletter")
    clock.now += timedelta(days=1)
    ai.error = AIUnavailable("busy")
    response = client.get("/newsletter")
    assert response.status_code == 200
    assert AI_BUSY in response.text and "Jazz Night" in response.text


def test_ai_busy_with_no_list_yet_shows_the_message(conn, sites):
    client, ai = setup(conn, sites)
    ai.error = AIUnavailable("busy")
    sign_up_ana(client)
    response = client.get("/newsletter")
    assert response.status_code == 200 and AI_BUSY in response.text


def test_ai_busy_after_a_preferences_change_tries_again_next_time(conn, sites):
    client, ai = setup(conn, sites)
    signed_in_ana(client)
    client.get("/newsletter")
    db.update_profile(conn, ana_id(client), "Ana", "comedy", "", ["Friday"], NOW + timedelta(minutes=5))
    ai.error = AIUnavailable("busy")
    client.get("/newsletter")
    ai.error = None
    client.get("/newsletter")
    assert ai.calls == 3
    client.get("/newsletter")
    assert ai.calls == 3


def test_signing_in_shows_the_saved_list_without_the_ai(conn, sites):
    client, ai = setup(conn, sites)
    sign_up_ana(client)
    client.get("/newsletter")
    client.supabase.confirm(GOOD["email"])
    client.cookies.clear()
    client.post("/signin", data={"email": GOOD["email"], "password": GOOD["password"]})
    assert "Jazz Night" in client.get("/newsletter").text
    assert ai.calls == 1


def test_list_page_has_no_copy_or_mark_as_sent(conn, sites):
    client, ai = setup(conn, sites)
    signed_in_ana(client)
    html = client.get("/newsletter").text
    assert 'id="copy"' not in html and "/newsletter/sent" not in html
    assert 'href="/preferences"' in html


def test_only_new_lists_count_toward_the_per_reader_limit(conn, sites):
    client, ai = setup(conn, sites)
    signed_in_ana(client)
    for _ in range(LIMITS["newsletter_account"][0] + 5):
        assert client.get("/newsletter").status_code == 200
    assert ai.calls == 1


def test_an_unusable_ai_answer_is_not_saved_for_the_day(conn, sites):
    client, ai = setup(conn, sites)
    signed_in_ana(client)
    ai.how_many = 0  # valid JSON, no picks, while events are on offer
    response = client.get("/newsletter")
    assert AI_BUSY in response.text
    ai.how_many = 2
    assert "Jazz Night" in client.get("/newsletter").text
    assert ai.calls == 2


def test_with_nothing_new_to_offer_the_empty_list_is_saved(conn, sites):
    ai = ChoosingAI()
    client = client_for(conn, sites, ai)
    signed_in_ana(client)
    client.get("/newsletter")
    client.get("/newsletter")
    assert ai.calls == 0  # no events at all: nothing to ask, and nothing to ask again


def test_yesterdays_list_shown_when_busy_keeps_its_own_date(conn, sites):
    clock = Clock(datetime(2026, 11, 2, 12, tzinfo=NY))
    client, ai = setup(conn, sites, clock)
    signed_in_ana(client)
    client.get("/newsletter")
    clock.now = datetime(2026, 11, 3, 12, tzinfo=NY)
    ai.error = AIUnavailable("busy")
    html = client.get("/newsletter").text
    assert "Monday, November 2" in html and "Tuesday, November 3" not in html
