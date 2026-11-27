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


def test_a_missed_hour_is_caught_up_later_that_day(conn, sites):
    ana(conn, sites, send_hour="7")
    resend, _ = run(conn)  # noon, five hours late (the server was down at 7)
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
    ana(conn, sites)
    busy = ChoosingAI()
    busy.error = AIUnavailable("busy")
    resend, result = run(conn, ai=busy)
    assert resend.sent == [] and result == (0, 1)
    later, _ = run(conn, now=NOW + timedelta(hours=1))
    assert len(later.sent) == 1


def test_a_failed_send_is_tried_again_next_hour(conn, sites):
    ana(conn, sites)
    _, result = run(conn, resend=Resend(ok=False))
    assert result == (0, 1)
    later, _ = run(conn, now=NOW + timedelta(hours=1))
    assert len(later.sent) == 1
