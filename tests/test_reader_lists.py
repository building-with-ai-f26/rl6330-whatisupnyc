"""Several lists on one account, like "Music" and "Sport", each with its own interests, days and email time."""
import json

import httpx

from app import db
from tests.fakes import ChoosingAI
from tests.test_daily_email import MONDAY, Resend
from tests.test_main import GOOD, NOW, ana_id, client_for, open_list, sign_up_ana, signed_in_ana
from tests.test_saved_lists import add_events

SPORT = {"title": "Sport", "interests": "basketball and running", "days": ["Saturday", "Sunday"], "send_hour": "9"}


def lists(conn, client):
    return db.reader_lists(conn, ana_id(client))


def ready(conn, sites, ai=None):
    add_events(conn, *[f"Event {n}" for n in range(12)])
    ai = ai or ChoosingAI()
    client = client_for(conn, sites, ai)
    signed_in_ana(client)
    return client, ai


def add_sport(client):
    return client.post("/lists/new", data=SPORT)


def test_sign_up_makes_one_list(conn, sites):
    client = client_for(conn, sites)
    sign_up_ana(client)
    assert [title for _, title in lists(conn, client)] == ["My list"]


def test_adding_a_list_opens_it(conn, sites):
    client, ai = ready(conn, sites)
    response = add_sport(client)
    (_, first), (sport, title) = [(i, t) for i, t in lists(conn, client)]
    assert title == "Sport"
    assert (response.status_code, response.headers["location"]) == (303, f"/newsletter?list={sport}")


def test_each_list_is_picked_from_its_own_interests(conn, sites):
    client, ai = ready(conn, sites)
    add_sport(client)
    (first, _), (sport, _) = lists(conn, client)
    open_list(client, f"/newsletter?list={sport}")
    assert "basketball and running" in ai.prompts[-1] and GOOD["interests"] not in ai.prompts[-1].split("\n")[0]


def test_each_list_keeps_its_own_daily_list(conn, sites):
    client, ai = ready(conn, sites)
    add_sport(client)
    (first, _), (sport, _) = lists(conn, client)
    open_list(client, f"/newsletter?list={first}")
    open_list(client, f"/newsletter?list={sport}")
    open_list(client, f"/newsletter?list={first}")
    open_list(client, f"/newsletter?list={sport}")
    assert ai.calls == 2


def test_two_lists_never_show_the_same_event(conn, sites):
    client, ai = ready(conn, sites)
    add_sport(client)
    (first, _), (sport, _) = lists(conn, client)
    open_list(client, f"/newsletter?list={first}")
    open_list(client, f"/newsletter?list={sport}")
    one = db.latest_issue(conn, ana_id(client), first).picks
    two = db.latest_issue(conn, ana_id(client), sport).picks
    assert not {p.event_id for p in one} & {p.event_id for p in two}


def test_the_list_page_has_a_tab_for_each_list(conn, sites):
    client, ai = ready(conn, sites)
    add_sport(client)
    (first, _), (sport, _) = lists(conn, client)
    html = open_list(client, f"/newsletter?list={sport}").text
    nav = html[html.index('aria-label="Your lists"'):]
    nav = nav[:nav.index("</nav>")]
    assert 'href="/newsletter">My list' in nav  # the first list keeps the plain address
    assert f'href="/newsletter?list={sport}" aria-current="page">Sport' in nav
    assert 'href="/lists/new"' in nav


def test_at_most_three_lists(conn, sites):
    client, ai = ready(conn, sites)
    for title in ("Sport", "Music", "Food"):
        client.post("/lists/new", data={**SPORT, "title": title})
    assert [t for _, t in lists(conn, client)] == ["My list", "Sport", "Music"]
    html = client.get("/newsletter").text
    assert 'href="/lists/new"' not in html


def test_another_readers_list_is_never_shown_or_changed(conn, sites):
    client, ai = ready(conn, sites)
    add_sport(client)
    (_, _), (sport, _) = lists(conn, client)
    other = client  # the same browser, now signed in as Bo
    other.cookies.clear()
    other.post("/subscribers", data={**GOOD, "email": "bo@x.test", "interests": "opera"})
    client.supabase.confirm("bo@x.test")
    other.cookies.clear()
    other.post("/signin", data={"email": "bo@x.test", "password": GOOD["password"]})
    page = other.get(f"/preferences?list={sport}").text
    assert "basketball" not in page and "opera" in page
    other.post(f"/preferences?list={sport}", data={"name": "Bo", "title": "Hacked", "interests": "x",
                                                   "days": ["Monday"], "send_hour": "8"})
    assert [t for _, t in lists(conn, client)] == ["My list", "Sport"]


def test_preferences_edit_only_the_list_you_are_on(conn, sites):
    client, ai = ready(conn, sites)
    add_sport(client)
    (first, _), (sport, _) = lists(conn, client)
    client.post(f"/preferences?list={sport}", data={"name": "Ana", "title": "Running", "interests": "marathons",
                                                    "days": ["Sunday"], "send_hour": "7", "email_on": "on"})
    assert [t for _, t in lists(conn, client)] == ["My list", "Running"]
    assert db.get_profile(conn, ana_id(client), first).interests == GOOD["interests"]
    assert db.get_profile(conn, ana_id(client), sport).interests == "marathons"
    assert db.email_settings(conn, ana_id(client), sport) == (7, True)


def test_a_list_can_be_deleted_but_not_the_last_one(conn, sites):
    client, ai = ready(conn, sites)
    add_sport(client)
    (first, _), (sport, _) = lists(conn, client)
    response = client.post(f"/lists/delete?list={sport}")
    assert response.headers["location"] == "/newsletter"
    assert [i for i, _ in lists(conn, client)] == [first]
    client.post(f"/lists/delete?list={first}")
    assert [i for i, _ in lists(conn, client)] == [first]


def test_more_like_these_stays_on_its_list(conn, sites):
    client, ai = ready(conn, sites)
    add_sport(client)
    (first, _), (sport, _) = lists(conn, client)
    open_list(client, f"/newsletter?list={first}")
    open_list(client, f"/newsletter?list={sport}")
    before = db.latest_issue(conn, ana_id(client), first).picks
    picked = db.latest_issue(conn, ana_id(client), sport).picks[0].event_id
    response = client.post(f"/newsletter/more?list={sport}", data={"event_ids": [picked]})
    assert response.headers["location"] == f"/newsletter?list={sport}"
    assert db.latest_issue(conn, ana_id(client), first).picks == before


def test_each_list_has_its_own_daily_email(conn, sites):
    add_events(conn, *[f"Event {n}" for n in range(12)])
    client = client_for(conn, sites, ChoosingAI())
    sign_up_ana(client, days=[MONDAY], send_hour="9")
    client.supabase.confirm(GOOD["email"])
    client.cookies.clear()
    client.post("/signin", data={"email": GOOD["email"], "password": GOOD["password"]})
    client.post("/lists/new", data={**SPORT, "days": [MONDAY], "send_hour": "12"})
    from app.daily_email import send_due_lists
    resend = Resend()
    sent, failed = send_due_lists(conn, ChoosingAI(), httpx.Client(transport=httpx.MockTransport(resend)), "k",
                                  "https://whatisupnyc.com", NOW)
    subjects = sorted(email["subject"] for email in resend.sent)
    assert (sent, failed) == (2, 0)
    assert subjects == ["Your Sport list for Monday, November 2", "Your whatisupnyc list for Monday, November 2"]
    assert json.dumps(resend.sent).count(GOOD["email"]) == 2


def test_two_adds_at_once_cannot_pass_the_limit(conn, sites):
    # Found by the automatic security review: both requests could count 2 lists and both add one.
    import threading

    client, ai = ready(conn, sites)
    add_sport(client)
    user, barrier, results = ana_id(client), threading.Barrier(2), []

    def add(title):
        barrier.wait()
        results.append(db.add_list(conn, user, title, "x", ["Monday"], 8, NOW))

    threads = [threading.Thread(target=add, args=(t,)) for t in ("Music", "Food")]
    for _ in range(20):
        for t in list(db.reader_lists(conn, user))[2:]:
            db.delete_list(conn, user, t[0])
        results.clear()
        threads = [threading.Thread(target=add, args=(t,)) for t in ("Music", "Food")]
        barrier.reset()
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(db.reader_lists(conn, user)) <= db.MAX_LISTS


# From the review (2026-11-28).
def test_saving_a_deleted_list_never_changes_another_list(conn, sites):
    client, ai = ready(conn, sites)
    add_sport(client)
    (first, _), (sport, _) = lists(conn, client)
    client.post(f"/lists/delete?list={sport}")
    response = client.post(f"/preferences?list={sport}", data={"name": "Ana", "title": "Sport", "interests": "x",
                                                               "days": ["Monday"], "send_hour": "7"})
    assert response.status_code == 404
    assert db.get_profile(conn, ana_id(client), first).interests == GOOD["interests"]
    assert lists(conn, client) == [(first, "My list")]


def test_deleting_twice_never_deletes_another_list(conn, sites):
    client, ai = ready(conn, sites)
    add_sport(client)
    client.post("/lists/new", data={**SPORT, "title": "Music"})
    (first, _), (sport, _), (music, _) = lists(conn, client)
    client.post(f"/lists/delete?list={sport}")
    client.post(f"/lists/delete?list={sport}")  # a double click, or Back and submit again
    assert [i for i, _ in lists(conn, client)] == [first, music]


def test_delete_needs_the_list_named(conn, sites):
    client, ai = ready(conn, sites)
    add_sport(client)
    client.post("/lists/delete")
    assert len(lists(conn, client)) == 2


def test_a_reader_left_without_a_list_gets_one_back(conn, sites):
    client, ai = ready(conn, sites)
    with conn.connection() as c:
        c.execute("delete from reader_lists")
    assert client.get("/newsletter").headers["location"] == "/preferences"
    client.post("/preferences", data={"name": "Ana", "interests": "jazz", "days": ["Friday"], "send_hour": "8",
                                      "email_on": "on"})
    assert [t for _, t in lists(conn, client)] == ["My list"]
    assert open_list(client).status_code == 200


def test_readers_can_change_only_their_lists_settings(conn, sites):
    import psycopg
    import pytest

    client, ai = ready(conn, sites)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.as_reader(conn, ana_id(client)) as c:
            c.execute("update reader_lists set emailed_on = now() where user_id = %s", (ana_id(client),))


def test_two_lists_with_the_same_name_each_get_their_email(conn, sites):
    add_events(conn, *[f"Event {n}" for n in range(12)])
    client = client_for(conn, sites, ChoosingAI())
    sign_up_ana(client, days=[MONDAY], send_hour="9")
    client.supabase.confirm(GOOD["email"])
    client.cookies.clear()
    client.post("/signin", data={"email": GOOD["email"], "password": GOOD["password"]})
    client.post("/lists/new", data={**SPORT, "title": "My list", "days": [MONDAY], "send_hour": "9"})
    keys = []

    def resend(request):
        keys.append(request.headers.get("idempotency-key"))
        return httpx.Response(200, json={})

    from app.daily_email import send_due_lists
    send_due_lists(conn, ChoosingAI(), httpx.Client(transport=httpx.MockTransport(resend)), "k", "https://x", NOW)
    assert len(keys) == 2 and len(set(keys)) == 2
