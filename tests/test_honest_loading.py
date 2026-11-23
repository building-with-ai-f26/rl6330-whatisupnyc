"""The "Making your list…" screen shows only while the AI is really making a list.

Signing in, confirming an email and choosing a new password never show it. Opening the list when a
new one is needed first returns a quick page with the making screen, which goes straight on to
/newsletter?make=1, where the AI makes the list. A saved list opens directly.
"""
from tests.fakes import FakeAI
from tests.test_main import GOOD, client_for, signed_in_ana
from tests.test_saved_lists import setup

HOP = "/newsletter?make=1"


def form_tag(html: str, action: str) -> str:
    start = html.index(f'action="{action}"')
    return html[html.rindex("<form", 0, start):html.index(">", start) + 1]


def test_sign_in_confirm_and_reset_forms_show_no_making_screen(conn, sites):
    client = client_for(conn, sites)
    pages = {"/signin": "/signin", "/reset?token_hash=x&type=recovery": "/reset", "/confirm?token_hash=x&type=email": "/confirm"}
    for path, action in pages.items():
        html = client.get(path).text
        tag = form_tag(html, action)
        assert "data-making" not in tag, path
        assert "data-busy" in tag, path  # the button says it's working instead
        assert 'id="making"' not in html, path
    assert 'data-busy="Signing in…"' in form_tag(client.get("/signin").text, "/signin")


def test_wrong_password_shows_the_message_without_a_making_screen(conn, sites):
    client = client_for(conn, sites)
    signed_in_ana(client)
    client.cookies.clear()
    response = client.post("/signin", data={"email": GOOD["email"], "password": "not-her-password"})
    assert response.status_code == 400
    assert "Wrong email or password." in response.text
    assert "Getting your list" not in response.text and "Making your list" not in response.text
    assert 'id="making"' not in response.text


def test_a_saved_list_opens_directly(conn, sites):
    client, ai = setup(conn, sites)
    signed_in_ana(client)
    client.get(HOP)  # today's list is made
    response = client.get("/newsletter")
    assert response.status_code == 200
    assert "Jazz Night" in response.text and 'class="flyer"' in response.text
    assert HOP not in response.text and "Making your list" not in response.text.split('id="more"')[0]
    assert ai.calls == 1


def test_a_new_list_first_shows_the_making_page_then_makes_it(conn, sites):
    client, ai = setup(conn, sites)
    signed_in_ana(client)
    quick = client.get("/newsletter")
    assert quick.status_code == 200
    assert ai.calls == 0
    assert "Making your list" in quick.text and 'class="making" id="making"' in quick.text
    assert f'location.replace("{HOP}")' in quick.text  # with JavaScript
    assert f'<meta http-equiv="refresh" content="0; url={HOP}">' in quick.text  # without it
    assert "Jazz Night" not in quick.text

    made = client.get(HOP)
    assert made.status_code == 200
    assert ai.calls == 1
    assert "Jazz Night" in made.text and 'class="flyer"' in made.text
    assert f'location.replace("{HOP}")' not in made.text


def test_the_make_request_with_a_saved_list_just_shows_it(conn, sites):
    # Reloading /newsletter?make=1, or opening it twice, never makes a second list.
    client, ai = setup(conn, sites)
    signed_in_ana(client)
    client.get(HOP)
    response = client.get(HOP)
    assert response.status_code == 200 and "Jazz Night" in response.text
    assert ai.calls == 1


def test_a_renewed_login_on_the_make_request_comes_back_to_it(conn, sites):
    ai = FakeAI(error=RuntimeError("unexpected"))
    client = client_for(conn, sites, ai)
    signed_in_ana(client)
    client.supabase.expire(client.cookies["wnyc_access"])
    response = client.get(HOP)
    assert (response.status_code, response.headers["location"]) == (303, HOP)
    assert ai.calls == 0
    assert client.cookies["wnyc_access"] in client.supabase.access


def test_more_like_these_says_it_is_making_a_list(conn, sites):
    client, ai = setup(conn, sites)
    signed_in_ana(client)
    html = client.get(HOP).text
    assert "data-making" in form_tag(html, "/newsletter/more")
    assert "Making your list" in html and "Getting your list" not in html
