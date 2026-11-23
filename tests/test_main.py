from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db, main
from app.ai import AIUnavailable
from app.limits import LIMITS, RateLimiter
from app.main import AppDeps, create_app
from app.models import Score
from tests.conftest import NY, make_event
from tests.fakes import FakeAI, FakeAuth, FakeTavily

NOW = datetime(2026, 11, 2, 12, tzinfo=NY)
GOOD = {
    "name": "Ana", "email": "ana@x.test", "password": "long-enough-1", "interests": "jazz", "look_prompt": "",
    "days": ["Friday"],
}


def client_for(conn, sites, ai=None, auth=None, posthog_key="", clock=None, resend_key="", outside=None, intake_hash=""):
    """A browser on the site. Its Supabase stand-in is client.supabase; requests the server makes to
    other services go to outside(request), or get a 404."""
    http = httpx.Client(transport=httpx.MockTransport(outside or (lambda r: httpx.Response(404))))
    auth = auth or FakeAuth(conn)
    deps = AppDeps(
        conn=conn, ai=ai or FakeAI(), tavily=FakeTavily(), http=http, sites=sites, clock=clock or (lambda: NOW),
        auth=auth, limiter=RateLimiter(LIMITS), posthog_key=posthog_key, resend_key=resend_key, intake_hash=intake_hash,
    )
    # https, so the browser sends the Secure login cookies back.
    # Requests arrive from Caddy on this machine, as in production, so X-Forwarded-For counts.
    client = TestClient(create_app(deps), base_url="https://testserver", follow_redirects=False, client=("127.0.0.1", 50000))
    client.headers["Origin"] = "https://testserver"
    client.supabase = auth
    return client


def sign_up_ana(client, **changes):
    """Makes Ana's account from the start page. She can see her first list right away."""
    return client.post("/subscribers", data={**GOOD, **changes})


def signed_in_ana(client):
    """Ana with a confirmed account, signed in with her password."""
    sign_up_ana(client)
    client.supabase.confirm(GOOD["email"])
    client.cookies.clear()
    return client.post("/signin", data={"email": GOOD["email"], "password": GOOD["password"]})


def open_list(client):
    """Opens /newsletter as a browser would: when it answers with the quick "Making your list…"
    page, goes on to the request that makes the list, and returns that answer."""
    response = client.get("/newsletter")
    if f'location.replace("{main.MAKE_LIST}")' in response.text:
        return client.get(main.MAKE_LIST)
    return response


def ana_id(client) -> str:
    return client.supabase.accounts[GOOD["email"]]["id"]


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
    client = client_for(conn, sites)
    response = sign_up_ana(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/newsletter"
    assert db.get_profile(conn, ana_id(client)).days == ["Friday"]


@pytest.mark.parametrize("field", ["interests", "name", "email", "password", "days"])
def test_missing_required_field_rejected(conn, sites, field):
    data = {**GOOD, field: "   " if field != "days" else []}
    assert client_for(conn, sites).post("/subscribers", data=data).status_code == 400


def test_newsletter_shows_picks(conn, sites):
    event_id = db.save_event(conn, make_event(name="Jazz Night"), Score(40, True))
    ai = FakeAI({"picks": [{"id": event_id, "reason": "You like jazz"}]})
    client = client_for(conn, sites, ai)
    sign_up_ana(client)
    html = open_list(client).text
    assert "Jazz Night" in html and "You like jazz" in html


def test_ai_down_shows_plain_message(conn, sites):
    db.save_event(conn, make_event(), Score(40, True))
    client = client_for(conn, sites, FakeAI(error=AIUnavailable("busy")))
    sign_up_ana(client)
    response = open_list(client)
    assert response.status_code == 200
    assert "try again" in response.text.lower()


def test_paste_shows_message(conn, sites):
    response = client_for(conn, sites).post("/paste", data={"link": "not a link", "caption": "x"})
    assert "link" in response.text.lower()


def test_newsletter_keeps_user_text_escaped(conn, sites):
    client = client_for(conn, sites)
    sign_up_ana(client, name="<script>x</script>")
    assert "<script>x" not in client.get("/newsletter").text


def test_start_page_shows_the_logo_and_tab_icon(conn, sites):
    html = client_for(conn, sites).get("/").text
    assert 'aria-label="whatisupnyc"' in html
    assert 'rel="icon"' in html


def test_day_buttons_keep_full_day_names_for_screen_readers(conn, sites):
    html = client_for(conn, sites).get("/").text
    assert 'value="Monday"' in html
    assert ">Mon<" in html


def test_start_page_lists_nobody(conn, sites):
    client = client_for(conn, sites)
    sign_up_ana(client, name="Ana Tester")
    client.cookies.clear()
    html = client.get("/").text
    assert "Ana Tester" not in html and "ana@x.test" not in html


def test_newsletter_page_shows_the_logo(conn, sites):
    client = client_for(conn, sites)
    sign_up_ana(client)
    assert 'aria-label="whatisupnyc"' in open_list(client).text


def test_no_posthog_key_means_no_tracking(conn, sites):
    assert "posthog" not in client_for(conn, sites).get("/").text


def test_posthog_loads_on_every_page_when_key_set(conn, sites):
    client = client_for(conn, sites, posthog_key="phc_test")
    sign_up_ana(client)
    # The quick "Making your list…" page sends nothing, so each visit to the list is one page view.
    assert "posthog" not in client.get("/newsletter").text
    client.get(main.MAKE_LIST)
    for path in ["/", "/newsletter", "/signin", "/forgot"]:
        html = client.get(path).text
        assert 'posthog.init("phc_test"' in html, path
        assert "before_send" in html


def test_rejected_form_keeps_what_the_reader_typed(conn, sites):
    data = {**GOOD, "interests": "late jazz <and> rooftop films", "look_prompt": "short", "email": "", "days": ["Friday", "Sunday"]}
    html = client_for(conn, sites).post("/subscribers", data=data).text
    assert GOOD["password"] not in html
    assert "late jazz &lt;and&gt; rooftop films</textarea>" in html
    assert 'value="Ana"' in html and 'value="short"' in html
    assert 'value="Friday" aria-label="Friday" checked' in html
    assert 'value="Sunday" aria-label="Sunday" checked' in html
    assert 'value="Monday" aria-label="Monday" checked' not in html


def test_paste_is_saved_for_later_without_waiting_on_the_ai(conn, sites):
    ai = FakeAI()
    client = client_for(conn, sites, ai=ai)
    for n in (1, 2):
        reply = client.post("/paste", data={"link": f"https://www.tiktok.com/@a/{n}", "caption": "Jazz, Nov 7"},
                            headers={"Accept": "application/json"})
        assert reply.status_code == 200 and reply.json()["added"] is True
    assert [link for _, link, _ in db.list_waiting_pastes(conn)] == ["https://www.tiktok.com/@a/1", "https://www.tiktok.com/@a/2"]
    assert ai.calls == 0


def test_paste_with_bad_link_is_refused_and_not_saved(conn, sites):
    reply = client_for(conn, sites).post("/paste", data={"link": "not a link", "caption": "x"},
                                         headers={"Accept": "application/json"})
    assert reply.status_code == 400
    assert reply.json() == {"added": False, "message": "That link doesn't look right. Please paste the full link to the post."}
    assert db.list_waiting_pastes(conn) == []


def test_start_page_has_a_loading_screen_for_making_the_list(conn, sites):
    html = client_for(conn, sites).get("/").text
    assert '<div class="making" id="making" role="status" aria-live="polite" hidden>' in html
    assert "Making your list" in html


def test_paste_that_is_too_long_is_refused(conn, sites):
    reply = client_for(conn, sites).post("/paste", data={"link": "https://www.tiktok.com/@a/1", "caption": "x" * 4001},
                                         headers={"Accept": "application/json"})
    assert reply.status_code == 400 and db.list_waiting_pastes(conn) == []


def test_paste_refused_when_too_many_are_waiting(conn, sites, monkeypatch):
    monkeypatch.setattr("app.main.MAX_WAITING_PASTES", 1)
    client = client_for(conn, sites)
    data = {"link": "https://www.tiktok.com/@a/1", "caption": "Jazz, Nov 7"}
    assert client.post("/paste", data=data, headers={"Accept": "application/json"}).status_code == 200
    assert client.post("/paste", data=data, headers={"Accept": "application/json"}).status_code == 400
    assert len(db.list_waiting_pastes(conn)) == 1


def test_default_deps_need_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("app.main.load_dotenv", lambda: None)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        main._default_deps()
