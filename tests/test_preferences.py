from datetime import timedelta

from app import db
from tests.fakes import ChoosingAI
from tests.test_main import GOOD, ana_id, client_for, sign_up_ana, signed_in_ana
from tests.test_saved_lists import Clock, add_events

NEW = {"name": "Ana B", "interests": "stand-up comedy", "look_prompt": "keep it short", "days": ["Saturday", "Sunday"]}


def test_preferences_form_is_prefilled(conn, sites):
    client = client_for(conn, sites)
    sign_up_ana(client, interests="late jazz", look_prompt="casual", days=["Friday", "Sunday"])
    client.supabase.confirm(GOOD["email"])
    client.cookies.clear()
    client.post("/signin", data={"email": GOOD["email"], "password": GOOD["password"]})
    html = client.get("/preferences").text
    assert 'value="Ana"' in html and "late jazz</textarea>" in html and 'value="casual"' in html
    assert 'value="Friday" checked' in html and 'value="Sunday" checked' in html
    assert 'value="Monday" checked' not in html


def test_saving_preferences_makes_a_new_list(conn, sites):
    add_events(conn, "Jazz Night", "Comedy Cellar", "Museum Late", "Park Walk")
    ai = ChoosingAI()
    clock = Clock()
    client = client_for(conn, sites, ai, clock=clock)
    signed_in_ana(client)
    client.get("/newsletter")
    clock.now += timedelta(minutes=1)
    response = client.post("/preferences", data=NEW)
    assert (response.status_code, response.headers["location"]) == (303, "/newsletter")
    saved = db.get_profile(conn, ana_id(client))
    assert (saved.name, saved.interests, saved.look_prompt, saved.days) == (
        "Ana B", "stand-up comedy", "keep it short", ["Saturday", "Sunday"]
    )
    client.get("/newsletter")
    client.get("/newsletter")
    assert ai.calls == 2


def test_incomplete_preferences_are_rejected_and_kept(conn, sites):
    client = client_for(conn, sites)
    signed_in_ana(client)
    for bad in ({**NEW, "days": []}, {**NEW, "interests": "   "}, {**NEW, "name": ""}):
        response = client.post("/preferences", data=bad)
        assert response.status_code == 400
        assert 'value="keep it short"' in response.text
    assert db.get_profile(conn, ana_id(client)).interests == GOOD["interests"]


def test_signed_out_and_first_look_readers_go_to_sign_in(conn, sites):
    client = client_for(conn, sites)
    for method in ("get", "post"):
        response = getattr(client, method)("/preferences", **({"data": NEW} if method == "post" else {}))
        assert (response.status_code, response.headers["location"]) == (303, "/signin")
    sign_up_ana(client)  # just signed up: a first look, not signed in
    assert client.get("/preferences").headers["location"] == "/signin"
    assert client.post("/preferences", data=NEW).headers["location"] == "/signin"
    assert db.get_profile(conn, ana_id(client)).interests == GOOD["interests"]


def test_reader_without_preferences_is_sent_to_make_them(conn, sites):
    client = client_for(conn, sites)
    signed_in_ana(client)
    with conn.connection() as c:
        c.execute("delete from profiles")
    response = client.get("/newsletter")
    assert (response.status_code, response.headers["location"]) == (303, "/preferences")
    assert client.get("/preferences").status_code == 200
    assert client.post("/preferences", data=NEW).status_code == 303
    assert db.get_profile(conn, ana_id(client)).interests == "stand-up comedy"
    assert client.get("/newsletter").status_code == 200


def test_preferences_form_refuses_other_sites(conn, sites):
    client = client_for(conn, sites)
    signed_in_ana(client)
    assert client.post("/preferences", data=NEW, headers={"Origin": "https://evil.example"}).status_code == 403
    assert db.get_profile(conn, ana_id(client)).interests == GOOD["interests"]


def test_preferences_text_is_escaped(conn, sites):
    client = client_for(conn, sites)
    signed_in_ana(client)
    client.post("/preferences", data={**NEW, "interests": "<script>x</script>"})
    assert "<script>x" not in client.get("/preferences").text


def test_pages_that_may_wait_show_the_making_screen(conn, sites):
    client = client_for(conn, sites)
    signed_in_ana(client)
    for path in ["/", "/signin", "/preferences", "/reset?token_hash=x&type=recovery", "/confirm?token_hash=x&type=email"]:
        assert 'id="making"' in client.get(path).text, path
