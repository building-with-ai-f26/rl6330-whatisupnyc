from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db
from app.ai import AIUnavailable
from app.main import AppDeps, create_app
from app.models import Score
from tests.conftest import NY, make_event
from tests.fakes import FakeAI, FakeTavily

NOW = datetime(2026, 11, 2, 12, tzinfo=NY)
GOOD = {"name": "Ana", "email": "ana@x.test", "interests": "jazz", "look_prompt": "", "days": ["Friday"]}


def client_for(conn, sites, ai=None):
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    deps = AppDeps(conn=conn, ai=ai or FakeAI(), tavily=FakeTavily(), http=http, sites=sites, clock=lambda: NOW)
    return TestClient(create_app(deps), follow_redirects=False)


def test_start_page_has_bar_templates_days(conn, sites):
    html = client_for(conn, sites).get("/").text
    assert "what kind of event?" in html.lower()
    for text in ["New to NYC", "Free and cheap", "Live music and comedy", "Art and culture"]:
        assert text in html
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
        assert day in html


def test_subscriber_saved_with_chosen_days(conn, sites):
    response = client_for(conn, sites).post("/subscribers", data=GOOD)
    assert response.status_code == 303
    assert db.get_subscriber(conn, 1).days == ["Friday"]


@pytest.mark.parametrize("field", ["interests", "name", "email", "days"])
def test_missing_required_field_rejected(conn, sites, field):
    data = {**GOOD, field: "   " if field != "days" else []}
    assert client_for(conn, sites).post("/subscribers", data=data).status_code == 400


def test_newsletter_shows_picks(conn, sites):
    event_id = db.save_event(conn, make_event(name="Jazz Night"), Score(40, True))
    db.save_subscriber(conn, "Ana", "ana@x.test", "jazz", "", ["Friday"])
    ai = FakeAI({"picks": [{"id": event_id, "reason": "You like jazz"}]})
    html = client_for(conn, sites, ai).get("/newsletter/1").text
    assert "Jazz Night" in html and "You like jazz" in html


def test_unknown_subscriber_is_404(conn, sites):
    assert client_for(conn, sites).get("/newsletter/99").status_code == 404


def test_ai_down_shows_plain_message(conn, sites):
    db.save_event(conn, make_event(), Score(40, True))
    db.save_subscriber(conn, "Ana", "ana@x.test", "jazz", "", ["Friday"])
    response = client_for(conn, sites, FakeAI(error=AIUnavailable("busy"))).get("/newsletter/1")
    assert response.status_code == 200
    assert "try again" in response.text.lower()


def test_mark_sent_excludes_next_time(conn, sites):
    event_id = db.save_event(conn, make_event(name="Jazz Night"), Score(40, True))
    db.save_subscriber(conn, "Ana", "ana@x.test", "jazz", "", ["Friday"])
    ai = FakeAI()
    client = client_for(conn, sites, ai)
    assert client.post("/newsletter/1/sent", data={"event_ids": [event_id]}).status_code == 303
    html = client.get("/newsletter/1").text
    assert "no new picks" in html.lower()
    assert ai.calls == 0


def test_paste_shows_message(conn, sites):
    response = client_for(conn, sites).post("/paste", data={"link": "not a link", "caption": "x"})
    assert "link" in response.text.lower()


def test_start_page_keeps_user_text_escaped(conn, sites):
    db.save_subscriber(conn, "<script>x</script>", "a@x.test", "jazz", "", ["Friday"])
    assert "<script>x" not in client_for(conn, sites).get("/").text


def test_start_page_shows_the_logo_and_tab_icon(conn, sites):
    html = client_for(conn, sites).get("/").text
    assert 'aria-label="whatisupnyc"' in html
    assert 'rel="icon"' in html


def test_day_buttons_keep_full_day_names_for_screen_readers(conn, sites):
    html = client_for(conn, sites).get("/").text
    assert 'value="Monday"' in html
    assert ">Mon<" in html


def test_people_list_comes_after_the_forms(conn, sites):
    db.save_subscriber(conn, "Ana", "a@x.test", "jazz", "", ["Friday"])
    html = client_for(conn, sites).get("/").text
    assert html.index("Make my list") < html.index("Ana") 
    assert html.index("Add this event") < html.index("Ana")


def test_newsletter_page_shows_the_logo(conn, sites):
    db.save_subscriber(conn, "Ana", "a@x.test", "jazz", "", ["Friday"])
    assert 'aria-label="whatisupnyc"' in client_for(conn, sites).get("/newsletter/1").text
