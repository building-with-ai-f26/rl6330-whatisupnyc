"""The daily email: each reader's list, at the hour they chose, on the days they chose."""
import json
from datetime import timedelta

import httpx

from app import db
from app.ai import AIUnavailable
from app.daily_email import send_due_lists
from tests.fakes import ChoosingAI
from tests.test_main import GOOD, NOW, ana_id, client_for, sign_up_ana
from tests.test_saved_lists import add_events

MONDAY = NOW.strftime("%A")  # NOW is Monday Nov 2, 12:00 in New York


class Resend:
    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def __call__(self, request):
        self.sent.append(json.loads(request.content))
        return httpx.Response(200 if self.ok else 500, json={})


def ana(conn, sites, confirmed=True, **changes):
    add_events(conn, "Jazz Night", "Poetry Slam")
    client = client_for(conn, sites, ChoosingAI())
    sign_up_ana(client, **{"days": [MONDAY], "send_hour": "9", **changes})
    if confirmed:
        client.supabase.confirm(GOOD["email"])
    return client


def run(conn, now=NOW, ai=None, resend=None):
    resend = resend or Resend()
    http = httpx.Client(transport=httpx.MockTransport(resend))
    result = send_due_lists(conn, ai or ChoosingAI(), http, "re_key", "https://whatisupnyc.com", now)
    return resend, result


def test_a_due_reader_gets_their_list_once_a_day(conn, sites):
    ana(conn, sites)
    resend, result = run(conn)
    (email,) = resend.sent
    assert email["to"] == [GOOD["email"]] and "Jazz Night" in email["html"]
    assert result == (1, 0)
    again, _ = run(conn, now=NOW + timedelta(hours=1))
    assert again.sent == []


def test_the_email_links_to_change_or_stop_it(conn, sites):
    ana(conn, sites)
    resend, _ = run(conn)
    assert "https://whatisupnyc.com/preferences" in resend.sent[0]["html"]


def test_nothing_before_their_hour(conn, sites):
    ana(conn, sites, send_hour="17")
    resend, _ = run(conn)
    assert resend.sent == []


def test_a_missed_hour_is_caught_up_within_three_hours(conn, sites):
    ana(conn, sites, send_hour="9")
    resend, _ = run(conn)  # noon, three hours late (the server was down at 9)
    assert len(resend.sent) == 1


def test_nothing_on_days_they_did_not_choose(conn, sites):
    ana(conn, sites)
    resend, _ = run(conn, now=NOW + timedelta(days=1))
    assert resend.sent == []


def test_nothing_when_they_turned_the_email_off(conn, sites):
    client = ana(conn, sites)
    db.set_email_settings(conn, ana_id(client), 9, False)
    resend, _ = run(conn)
    assert resend.sent == []


def test_nothing_until_they_confirm_their_email(conn, sites):
    ana(conn, sites, confirmed=False)
    resend, _ = run(conn)
    assert resend.sent == []


def test_todays_list_is_reused_without_asking_the_ai_again(conn, sites):
    client = ana(conn, sites)
    from app import saved_lists
    saved_lists.make_list(conn, ChoosingAI(), db.get_profile(conn, ana_id(client)), NOW)
    ai = ChoosingAI()
    resend, _ = run(conn, ai=ai)
    assert ai.calls == 0 and len(resend.sent) == 1


def test_when_the_ai_is_busy_it_tries_again_next_hour(conn, sites):
    ana(conn, sites, send_hour="12")
    busy = ChoosingAI()
    busy.error = AIUnavailable("busy")
    resend, result = run(conn, ai=busy)
    assert resend.sent == [] and result == (0, 1)
    later, _ = run(conn, now=NOW + timedelta(hours=1))
    assert len(later.sent) == 1


def test_a_failed_send_is_tried_again_next_hour(conn, sites):
    ana(conn, sites, send_hour="12")
    _, result = run(conn, resend=Resend(ok=False))
    assert result == (0, 1)
    later, _ = run(conn, now=NOW + timedelta(hours=1))
    assert len(later.sent) == 1


# From the review (2026-11-27).
def two_readers(conn, sites):
    add_events(conn, "Jazz Night", "Poetry Slam")
    client = client_for(conn, sites, ChoosingAI())
    for email in ("ana@x.test", "bo@x.test"):
        client.cookies.clear()
        sign_up_ana(client, email=email, days=[MONDAY], send_hour="9")
        client.supabase.confirm(email)
    return client


def test_one_reader_failing_does_not_stop_the_others(conn, sites, monkeypatch):
    two_readers(conn, sites)
    real = db.latest_issue
    calls = {"n": 0}

    def first_breaks(c, user_id, *rest):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("connection dropped")
        return real(c, user_id, *rest)

    monkeypatch.setattr(db, "latest_issue", first_breaks)
    resend, result = run(conn)
    assert len(resend.sent) == 1 and result == (1, 1)


def test_a_reader_is_claimed_before_sending_so_a_crash_never_sends_twice(conn, sites, monkeypatch):
    ana(conn, sites)
    monkeypatch.setattr(db, "mark_emailed", lambda *a: (_ for _ in ()).throw(RuntimeError("db down")), raising=False)
    first, _ = run(conn)
    again, _ = run(conn, now=NOW + timedelta(hours=1))
    assert len(first.sent) + len(again.sent) == 1


def test_resend_is_told_to_drop_a_repeat(conn, sites):
    client = ana(conn, sites)
    seen = []

    def resend(request):
        seen.append(request.headers.get("idempotency-key"))
        return httpx.Response(200, json={})

    http = httpx.Client(transport=httpx.MockTransport(resend))
    send_due_lists(conn, ChoosingAI(), http, "re_key", "https://whatisupnyc.com", NOW)
    assert seen == [f"daily-{ana_id(client)}-2026-11-02"]


def test_the_ai_limit_stops_the_run(conn, sites):
    from app.ai import DailyLimitReached
    two_readers(conn, sites)
    ai = ChoosingAI()
    ai.error = DailyLimitReached("limit")
    _, result = run(conn, ai=ai)
    assert ai.calls == 1 and result == (0, 1)


def test_room_is_left_for_the_website(conn, sites):
    from app.daily_email import AI_RESERVE
    ana(conn, sites)
    for _ in range(50 - AI_RESERVE):
        db.record_ai_request(conn, NOW)
    ai = ChoosingAI()
    resend, _ = run(conn, ai=ai)
    assert ai.calls == 0 and resend.sent == []


def test_retries_stop_after_the_three_hours_after_the_chosen_hour(conn, sites):
    ana(conn, sites, send_hour="8")  # NOW is noon: four hours late
    resend, _ = run(conn)
    assert resend.sent == []


def test_events_over_by_send_time_are_left_out(conn, sites):
    from tests.test_saved_lists import add_events as more
    client = ana(conn, sites)
    more(conn, "Morning Walk", start=NOW.replace(hour=10))
    from app import saved_lists
    saved_lists.make_list(conn, ChoosingAI(how_many=3), db.get_profile(conn, ana_id(client)), NOW.replace(hour=8))
    resend, _ = run(conn)
    assert "Morning Walk" not in resend.sent[0]["html"]


def test_no_email_when_there_is_nothing_new(conn, sites):
    client = ana(conn, sites)
    db.save_issue(conn, ana_id(client), [], NOW)  # today's list came back empty: everything was already sent
    resend, result = run(conn)
    assert resend.sent == [] and result == (0, 0)


def test_banned_and_deleted_accounts_get_nothing(conn, sites):
    ana(conn, sites)
    with conn.connection() as c:
        c.execute("update auth.users set banned_until = now() + interval '1 day'")
    banned, _ = run(conn)
    with conn.connection() as c:
        c.execute("update auth.users set banned_until = null, deleted_at = now()")
    deleted, _ = run(conn)
    assert banned.sent == [] and deleted.sent == []
