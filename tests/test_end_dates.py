"""Events that run for several days, like markets and sample sales, stay on lists until they end."""
from datetime import datetime, timedelta

from app import db
from app.models import Score, ScoredEvent
from app.newsletter import Pick, card, choose_picks, render_newsletter
from tests.conftest import NY, make_event
from tests.fakes import ChoosingAI
from tests.test_newsletter import SUB

NOW = datetime(2026, 11, 23, 12, tzinfo=NY)
MARKET = dict(name="Winter Market", start=datetime(2026, 10, 31, 11, tzinfo=NY), end=datetime(2027, 3, 1, 22, tzinfo=NY))


def scored(**fields):
    return ScoredEvent(1, make_event(**fields), Score(20, True))


def test_a_running_event_stays_listed_until_it_ends(conn):
    db.save_event(conn, make_event(**MARKET), Score(20, True))
    assert [e.event.name for e in db.eligible_events(conn, NOW)] == ["Winter Market"]
    (saved,) = db.eligible_events(conn, NOW)
    assert saved.event.end == MARKET["end"]
    assert db.eligible_events(conn, MARKET["end"] + timedelta(minutes=1)) == []


def test_a_running_event_says_open_until():
    item = card(scored(**MARKET), now=NOW)
    assert item["when"] == "Open until Mon, Mar 1"
    assert item["plate"] == "Until Mar 1"


def test_a_multi_day_event_not_yet_open_says_when_it_opens():
    item = card(scored(name="Holiday Fair", start=datetime(2026, 11, 28, 10, tzinfo=NY),
                       end=datetime(2026, 12, 24, 18, tzinfo=NY)), now=NOW)
    assert item["when"] == "From Sat, Nov 28, open until Thu, Dec 24"
    assert item["plate"] == "Sat Nov 28"


def test_a_one_day_event_reads_as_before():
    item = card(scored(name="Show", start=datetime(2026, 11, 27, 20, tzinfo=NY),
                       end=datetime(2026, 11, 27, 23, tzinfo=NY)), now=NOW)
    assert item["when"] == "Fri, Nov 27, 8:00 PM"


def test_the_email_says_open_until():
    html = render_newsletter([Pick(scored(**MARKET), "Fits: markets")], now=NOW)
    assert "Open until Mon, Mar 1" in html


def test_the_ai_is_told_how_long_it_runs():
    ai = ChoosingAI()
    choose_picks(SUB, [scored(**MARKET)], ai, now=NOW)
    assert '"open_until": "Mon Mar 1, 2027"' in ai.prompts[-1]


def test_a_late_night_party_is_not_a_multi_day_event():
    item = card(scored(name="Party", start=datetime(2026, 11, 27, 21, tzinfo=NY),
                       end=datetime(2026, 11, 28, 2, tzinfo=NY)), now=NOW)
    assert item["when"] == "Fri, Nov 27, 9:00 PM" and item["plate"] == "Fri Nov 27"


def test_an_end_at_midnight_counts_as_the_day_before():
    item = card(scored(name="Market", start=datetime(2026, 11, 20, 10, tzinfo=NY),
                       end=datetime(2026, 12, 1, 0, tzinfo=NY)), now=NOW)
    assert item["when"] == "Open until Mon, Nov 30" and item["plate"] == "Until Nov 30"


def test_a_running_event_is_happening_today():
    html = render_newsletter([Pick(scored(**MARKET), "Fits: markets")], now=NOW)
    assert html.index("Happening today") < html.index("Winter Market")
    ai = ChoosingAI()
    choose_picks(SUB, [scored(**MARKET)], ai, now=NOW)
    assert '"today": true' in ai.prompts[-1]
