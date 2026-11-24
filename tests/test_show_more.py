"""Show more (picked in the same AI call, already in the page) and More like these adding to the list."""
from app import db
from tests.fakes import ChoosingAI
from tests.test_dashboard import FETCH
from tests.test_main import ana_id, client_for, open_list, signed_in_ana
from tests.test_saved_lists import add_events

NAMES = [f"Event {n:02d}" for n in range(30)]


def big_list(conn, sites):
    add_events(conn, *NAMES)
    ai = ChoosingAI(16)
    client = client_for(conn, sites, ai)
    signed_in_ana(client)
    html = open_list(client).text
    return client, ai, html


def visible_and_extra(html):
    """Event names on cards shown at once, and on cards behind Show more."""
    shown, extra = [], []
    for match in html.split('<article class="flyer" id="event-')[1:]:
        name = next(n for n in NAMES if f">{n}<" in match.split("</article>")[0])
        (extra if "data-extra" in match.split(">")[0] else shown).append(name)
    return shown, extra


def test_one_ai_call_picks_sixteen(conn, sites):
    client, ai, html = big_list(conn, sites)
    assert ai.calls == 1 and "Choose at most 16." in ai.prompts[0]


def test_eight_show_and_eight_wait_behind_show_more(conn, sites):
    client, ai, html = big_list(conn, sites)
    shown, extra = visible_and_extra(html)
    assert len(shown) == 8 and len(extra) == 8 and not set(shown) & set(extra)
    assert 'id="show-more"' in html and "Show 8 more" in html
    assert 'href="#picks">Picks (8)' in html


def test_no_show_more_when_the_list_is_short(conn, sites):
    add_events(conn, *NAMES[:5])
    client = client_for(conn, sites, ChoosingAI(16))
    signed_in_ana(client)
    html = open_list(client).text
    assert 'id="show-more"' not in html and " data-extra hidden" not in html


def test_only_the_eight_shown_count_as_seen(conn, sites):
    client, ai, html = big_list(conn, sites)
    shown, extra = visible_and_extra(html)
    ids = {e.event.name: e.id for e in db.eligible_events(conn, ai_now(client))}
    sent = db.sent_event_ids(conn, ana_id(client))
    assert sent == {ids[n] for n in shown}


def ai_now(client):
    from tests.test_main import NOW
    return NOW


def test_revealing_the_extras_marks_them_seen(conn, sites):
    client, ai, html = big_list(conn, sites)
    shown, extra = visible_and_extra(html)
    ids = {e.event.name: e.id for e in db.eligible_events(conn, ai_now(client))}
    answer = client.post("/newsletter/shown", data={"event_ids": [ids[n] for n in extra]}, headers=FETCH)
    assert answer.json() == {"ok": True}
    assert db.sent_event_ids(conn, ana_id(client)) == {ids[n] for n in shown + extra}


def test_only_events_on_the_current_list_can_be_marked_seen(conn, sites):
    client, ai, html = big_list(conn, sites)
    listed = {e.event.name for e in db.eligible_events(conn, ai_now(client))} - set(sum(visible_and_extra(html), []))
    outside = next(e.id for e in db.eligible_events(conn, ai_now(client)) if e.event.name in listed)
    assert client.post("/newsletter/shown", data={"event_ids": [outside]}, headers=FETCH).status_code == 404
    assert outside not in db.sent_event_ids(conn, ana_id(client))


def test_the_email_has_only_the_eight_shown(conn, sites):
    client, ai, html = big_list(conn, sites)
    shown, extra = visible_and_extra(html)
    email = client.get("/newsletter/email").text
    assert all(n in email for n in shown) and not any(n in email for n in extra)


def test_more_like_these_adds_to_the_top_and_keeps_the_list(conn, sites):
    client, ai, html = big_list(conn, sites)
    shown, extra = visible_and_extra(html)
    ids = {e.event.name: e.id for e in db.eligible_events(conn, ai_now(client))}
    ticked = shown[0]
    client.post("/newsletter/more", data={"event_ids": [ids[ticked]]})
    after = client.get("/newsletter").text
    new_shown, new_extra = visible_and_extra(after)
    assert set(shown) <= set(new_shown)  # nothing disappears, the ticked event included
    added = [n for n in new_shown if n not in shown + extra]
    assert len(added) == 8
    heading = after.index("More like what you picked")
    first_old = min(after.index(f">{n}<") for n in shown)
    assert all(heading < after.index(f">{n}<") < first_old for n in added)
    assert db.saved_for_later_ids(conn, ana_id(client)) == []  # nothing to rescue any more


def test_more_like_these_never_repeats_what_is_already_on_the_list(conn, sites):
    client, ai, html = big_list(conn, sites)
    shown, extra = visible_and_extra(html)
    ids = {e.event.name: e.id for e in db.eligible_events(conn, ai_now(client))}
    client.post("/newsletter/more", data={"event_ids": [ids[shown[0]]]})
    names = [n for n in sum(visible_and_extra(client.get("/newsletter").text), [])]
    assert len(names) == len(set(names))


def test_more_like_these_says_it_adds(conn, sites):
    client, ai, html = big_list(conn, sites)
    assert "Adds events like the ones you ticked to the top of your list" in html
    assert "Finding more like these" in html


def test_revealed_picks_stay_shown_after_a_reload(conn, sites):
    client, ai, html = big_list(conn, sites)
    shown, extra = visible_and_extra(html)
    ids = {e.event.name: e.id for e in db.eligible_events(conn, ai_now(client))}
    client.post("/newsletter/shown", data={"event_ids": [ids[n] for n in extra]}, headers=FETCH)
    again = client.get("/newsletter").text
    now_shown, now_extra = visible_and_extra(again)
    assert set(now_shown) == set(shown + extra) and now_extra == []
    assert 'id="show-more"' not in again and 'href="#picks">Picks (16)' in again


def test_picks_never_revealed_are_not_offered_for_rating(conn, sites):
    client, ai, html = big_list(conn, sites)
    shown, extra = visible_and_extra(html)
    ids = {e.event.name: e.id for e in db.eligible_events(conn, ai_now(client))}
    history = db.history_event_ids(conn, ana_id(client))
    assert history == {ids[n] for n in shown}
    assert db.dashboard_data(conn, ana_id(client))[2] == {ids[n] for n in shown}


def test_more_like_these_on_yesterdays_list_starts_fresh(conn, sites):
    from datetime import timedelta

    from tests.test_main import NOW
    from tests.test_saved_lists import Clock

    add_events(conn, *NAMES, start=NOW + timedelta(days=5))
    clock = Clock()
    ai = ChoosingAI(16)
    client = client_for(conn, sites, ai, clock=clock)
    signed_in_ana(client)
    first, _ = visible_and_extra(open_list(client).text)
    ids = {e.event.name: e.id for e in db.eligible_events(conn, NOW)}
    clock.now = NOW + timedelta(days=1)  # the page was left open overnight
    client.post("/newsletter/more", data={"event_ids": [ids[first[0]]]})
    names = sum(visible_and_extra(client.get("/newsletter").text), [])
    assert not set(first) & set(names)  # yesterday's picks aren't carried into today's list


def test_the_same_show_on_another_night_is_not_added(conn, sites):
    from datetime import timedelta

    from tests.test_main import NOW

    add_events(conn, "Hamlet", start=NOW + timedelta(days=3))
    add_events(conn, "Opener")
    client = client_for(conn, sites, ChoosingAI(2))
    signed_in_ana(client)
    open_list(client)
    add_events(conn, "Hamlet", start=NOW + timedelta(days=4))  # Friday's performance, found later
    add_events(conn, "Something Else", start=NOW + timedelta(days=4, hours=2))
    hamlet = next(e.id for e in db.eligible_events(conn, NOW) if e.event.name == "Hamlet")
    client.post("/newsletter/more", data={"event_ids": [hamlet]})
    html = client.get("/newsletter").text
    # One pick card for Hamlet (each card also carries its name in its hidden saved card).
    assert html.count('data-name="Hamlet"><button class="plain toggle"') == 1


def test_show_more_during_a_slow_more_like_these_stays_shown(conn, sites):
    client, ai, html = big_list(conn, sites)
    shown, extra = visible_and_extra(html)
    ids = {e.event.name: e.id for e in db.eligible_events(conn, ai_now(client))}
    real = ai.complete_json

    def answer_after_another_tab_reveals(system, user):
        db.reveal_picks(conn, ana_id(client), [ids[n] for n in extra])  # tab B taps Show more meanwhile
        return real(system, user)

    ai.complete_json = answer_after_another_tab_reveals
    client.post("/newsletter/more", data={"event_ids": [ids[shown[0]]]})
    now_shown, now_extra = visible_and_extra(client.get("/newsletter").text)
    assert set(extra) <= set(now_shown)


def test_two_more_like_these_at_once_keep_both_batches(conn, sites):
    client, ai, html = big_list(conn, sites)
    shown, extra = visible_and_extra(html)
    ids = {e.event.name: e.id for e in db.eligible_events(conn, ai_now(client))}
    real, inner = ai.complete_json, {"done": False}

    def from_the_end(system, user):
        import json
        events = json.loads(user.split("Events: ", 1)[1])
        return {"picks": [{"id": e["id"], "reason": "B"} for e in events[-4:]]}

    def another_request_finishes_first(system, user):
        if not inner["done"]:
            inner["done"] = True
            ai.complete_json = from_the_end  # tab B's answer differs from tab A's
            inner["status"] = client.post("/newsletter/more", data={"event_ids": [ids[shown[1]]]}).status_code
            ai.complete_json = another_request_finishes_first
        return real(system, user)

    ai.complete_json = another_request_finishes_first
    ai.how_many = 2  # tab A picks the first two, tab B the last four
    client.post("/newsletter/more", data={"event_ids": [ids[shown[0]]]})
    assert inner.get("status") == 303, inner
    names = sum(visible_and_extra(client.get("/newsletter").text), [])
    sent = db.sent_event_ids(conn, ana_id(client))
    on_list = {ids[n] for n in names}
    assert sent <= on_list  # nothing marked seen has fallen off the list
