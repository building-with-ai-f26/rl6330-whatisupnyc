"""The shorter sign-up: three steps, every day in one click, a send time, and no list-style question."""
import re

from app import db
from tests.test_main import GOOD, NOW, ana_id, client_for, sign_up_ana, signed_in_ana


def start_form(html):
    return html[html.index('<form id="make"'):html.index("</form>", html.index('<form id="make"'))]


def test_sign_up_has_three_steps(conn, sites):
    form = start_form(client_for(conn, sites).get("/").text)
    assert re.findall(r'class="step[^"]*" data-name="(\w+)"', form) == ["interests", "when", "account"]


def test_name_email_and_password_share_one_step(conn, sites):
    form = start_form(client_for(conn, sites).get("/").text)
    account = form[form.index('data-name="account"'):]
    for field in ('name="name"', 'name="email"', 'name="password"'):
        assert field in account


def test_every_day_is_one_click(conn, sites):
    form = start_form(client_for(conn, sites).get("/").text)
    assert 'id="every-day"' in form and "Every day" in form


def test_the_list_style_question_is_gone(conn, sites):
    client = client_for(conn, sites)
    assert "look_prompt" not in client.get("/").text
    signed_in_ana(client)
    assert "look_prompt" not in client.get("/preferences").text


def test_readers_choose_when_the_email_arrives(conn, sites):
    form = start_form(client_for(conn, sites).get("/").text)
    assert 'name="send_hour"' in form
    assert re.findall(r'<option value="(\d+)"', form) == ["7", "8", "9", "12", "17"]
    assert '<option value="8" selected>' in form


def test_the_chosen_send_time_is_saved(conn, sites):
    client = client_for(conn, sites)
    sign_up_ana(client, send_hour="17")
    assert db.email_settings(conn, ana_id(client)) == (17, True)


def test_an_unknown_send_time_falls_back_to_8_am(conn, sites):
    client = client_for(conn, sites)
    sign_up_ana(client, send_hour="3")
    assert db.email_settings(conn, ana_id(client)) == (8, True)


def test_preferences_change_the_send_time_and_can_stop_the_email(conn, sites):
    client = client_for(conn, sites)
    signed_in_ana(client)
    html = client.get("/preferences").text
    assert 'name="send_hour"' in html and 'name="email_on"' in html
    client.post("/preferences", data={"name": "Ana", "interests": "jazz", "days": ["Friday"], "send_hour": "9"})
    assert db.email_settings(conn, ana_id(client)) == (9, False)  # box left unticked: no emails
    client.post("/preferences", data={"name": "Ana", "interests": "jazz", "days": ["Friday"], "send_hour": "12",
                                      "email_on": "on"})
    assert db.email_settings(conn, ana_id(client)) == (12, True)


def test_the_ai_is_no_longer_sent_a_style_request(conn, sites):
    from tests.fakes import ChoosingAI
    from tests.test_main import open_list
    from tests.test_saved_lists import add_events

    add_events(conn, "Jazz Night")
    ai = ChoosingAI()
    client = client_for(conn, sites, ai)
    signed_in_ana(client)
    open_list(client)
    assert "style request" not in ai.prompts[0]


def test_sign_up_without_a_send_time_still_works(conn, sites):
    client = client_for(conn, sites)
    assert sign_up_ana(client).status_code in (200, 303)
    assert db.email_settings(conn, ana_id(client)) == (8, True)
    assert GOOD["email"] and NOW
