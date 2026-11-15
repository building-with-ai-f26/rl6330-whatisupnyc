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
PASSWORD = "correct-horse-battery-staple"
GOOD = {"name": "Ana", "email": "ana@x.test", "interests": "jazz", "look_prompt": "", "days": ["Friday"]}


def client_for(conn, sites, ai=None, posthog_key=""):
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    deps = AppDeps(
        conn=conn, ai=ai or FakeAI(), tavily=FakeTavily(), http=http, sites=sites, clock=lambda: NOW,
        site_password=PASSWORD, posthog_key=posthog_key,
    )
    client = TestClient(create_app(deps), follow_redirects=False)
    client.auth = ("pod", PASSWORD)
    client.headers["Origin"] = "http://testserver"
    return client


def save_ana(conn, name="Ana") -> str:
    return db.subscriber_token(conn, db.save_subscriber(conn, name, "ana@x.test", "jazz", "", ["Friday"]))


def test_start_page_has_bar_templates_days(conn, sites):
    html = client_for(conn, sites).get("/").text
    assert "what kind of events do you like?" in html.lower()
    for text in ["New to NYC", "Free and cheap", "Live music and comedy", "Art and culture"]:
        assert text in html
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
        assert day in html


def start_form(html: str) -> str:
    start = html.index('<form id="make"')
    return html[start:html.index("</form>", start)]


def test_start_page_opens_with_explanation_then_big_interests_box(conn, sites):
    html = client_for(conn, sites).get("/").text
    form = start_form(html)
    intro = form.index('class="intro"')
    box = form.index('<textarea id="interests"')
    # The explanation and the interests box come before every other question.
    assert intro < box < form.index('type="checkbox" name="days"') < form.index('id="name"') < form.index('id="email"')
    assert "short list of NYC events" in form[intro:box]
    assert 'class="step current" data-name="interests"' in form


def test_start_page_has_every_field_in_one_form_so_it_works_without_javascript(conn, sites):
    html = client_for(conn, sites).get("/").text
    form = start_form(html)
    assert 'action="/subscribers"' in html and 'method="post"' in html
    for field in ['name="interests"', 'name="name"', 'name="email"', 'name="look_prompt"', 'name="days"']:
        assert field in form
    assert form.count('type="checkbox" name="days"') == 7
    assert 'type="submit"' in form
    # The paste form is a separate form, after the sign-up flow.
    assert 'action="/paste"' not in form and html.index('action="/paste#add"') > html.index("</form>")


def test_start_step_tracking_sends_no_typed_text(conn, sites):
    html = client_for(conn, sites, posthog_key="phc_test").get("/").text
    assert "start_step_viewed" in html and "start_form_submitted" in html
    script = html[html.rindex("<script>"):]
    for typed in ["box.value", "name.value", "email.value", "look_prompt.value"]:
        for line in script.splitlines():
            if "track(" in line:
                assert typed not in line


def test_server_error_shows_every_field_and_the_message(conn, sites):
    response = client_for(conn, sites).post("/subscribers", data={**GOOD, "days": []})
    assert response.status_code == 400
    assert "at least one day" in response.text
    # Not in step-by-step mode: every question shows at once.
    assert 'class="flow stepping"' not in response.text
    assert 'class="flow"' in response.text


def test_paste_message_shows_beside_paste_form_and_flow_keeps_its_steps(conn, sites):
    html = client_for(conn, sites).post("/paste", data={"link": "not a link", "caption": "x"}).text
    assert 'class="flow stepping"' in html
    assert '<details id="add" class="extra" open>' in html
    assert html.index('class="message"') > html.index('action="/paste#add"')


def test_subscriber_saved_with_chosen_days(conn, sites):
    response = client_for(conn, sites).post("/subscribers", data=GOOD)
    assert response.status_code == 303
    assert db.get_subscriber(conn, 1).days == ["Friday"]
    assert response.headers["location"] == f"/newsletter/{db.subscriber_token(conn, 1)}"


@pytest.mark.parametrize("field", ["interests", "name", "email", "days"])
def test_missing_required_field_rejected(conn, sites, field):
    data = {**GOOD, field: "   " if field != "days" else []}
    assert client_for(conn, sites).post("/subscribers", data=data).status_code == 400


def test_newsletter_shows_picks(conn, sites):
    event_id = db.save_event(conn, make_event(name="Jazz Night"), Score(40, True))
    token = save_ana(conn)
    ai = FakeAI({"picks": [{"id": event_id, "reason": "You like jazz"}]})
    html = client_for(conn, sites, ai).get(f"/newsletter/{token}").text
    assert "Jazz Night" in html and "You like jazz" in html


def test_unknown_subscriber_is_404(conn, sites):
    save_ana(conn)
    client = client_for(conn, sites)
    assert client.get("/newsletter/not-a-real-link").status_code == 404
    assert client.get("/newsletter/1").status_code == 404
    assert client.post("/newsletter/1/sent", data={"event_ids": [1]}).status_code == 404


def test_ai_down_shows_plain_message(conn, sites):
    db.save_event(conn, make_event(), Score(40, True))
    token = save_ana(conn)
    response = client_for(conn, sites, FakeAI(error=AIUnavailable("busy"))).get(f"/newsletter/{token}")
    assert response.status_code == 200
    assert "try again" in response.text.lower()


def test_mark_sent_excludes_next_time(conn, sites):
    event_id = db.save_event(conn, make_event(name="Jazz Night"), Score(40, True))
    token = save_ana(conn)
    ai = FakeAI()
    client = client_for(conn, sites, ai)
    assert client.post(f"/newsletter/{token}/sent", data={"event_ids": [event_id]}).status_code == 303
    html = client.get(f"/newsletter/{token}").text
    assert "no new picks" in html.lower()
    assert ai.calls == 0


def test_paste_shows_message(conn, sites):
    response = client_for(conn, sites).post("/paste", data={"link": "not a link", "caption": "x"})
    assert "link" in response.text.lower()


def test_newsletter_keeps_user_text_escaped(conn, sites):
    token = save_ana(conn, name="<script>x</script>")
    assert "<script>x" not in client_for(conn, sites).get(f"/newsletter/{token}").text


def test_start_page_shows_the_logo_and_tab_icon(conn, sites):
    html = client_for(conn, sites).get("/").text
    assert 'aria-label="whatisupnyc"' in html
    assert 'rel="icon"' in html


def test_day_buttons_keep_full_day_names_for_screen_readers(conn, sites):
    html = client_for(conn, sites).get("/").text
    assert 'value="Monday"' in html
    assert ">Mon<" in html


def test_start_page_lists_nobody(conn, sites):
    token = save_ana(conn, name="Ana Tester")
    html = client_for(conn, sites).get("/").text
    assert "Ana Tester" not in html and "ana@x.test" not in html and token not in html


def test_newsletter_page_shows_the_logo(conn, sites):
    token = save_ana(conn)
    assert 'aria-label="whatisupnyc"' in client_for(conn, sites).get(f"/newsletter/{token}").text


def test_no_posthog_key_means_no_tracking(conn, sites):
    assert "posthog" not in client_for(conn, sites).get("/").text


def test_posthog_loads_on_every_page_when_key_set(conn, sites):
    client = client_for(conn, sites, posthog_key="phc_test")
    token = save_ana(conn)
    for path in ["/", f"/newsletter/{token}"]:
        html = client.get(path).text
        assert 'posthog.init("phc_test"' in html
        assert "before_send" in html
