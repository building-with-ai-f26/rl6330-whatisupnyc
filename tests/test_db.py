from datetime import datetime, timedelta

import pytest

from app import db
from app.models import CandidateEvent, Score
from tests.conftest import NY, make_event

NOW = datetime(2026, 11, 2, 12, tzinfo=NY)


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        make_event(start=datetime(2026, 11, 7))


def test_eligible_events_excludes_ineligible_and_past(conn):
    db.save_event(conn, make_event(name="Good"), Score(40, True, None))
    db.save_event(conn, make_event(name="Weak"), Score(0, False, "no credibility"))
    db.save_event(conn, make_event(name="Old", start=NOW - timedelta(days=1)), Score(40, True, None))
    assert [e.event.name for e in db.eligible_events(conn, NOW)] == ["Good"]


def test_eligible_events_keep_time_zone_and_highest_score_first(conn):
    db.save_event(conn, make_event(name="Low"), Score(10, True, None))
    db.save_event(conn, make_event(name="High"), Score(40, True, None))
    events = db.eligible_events(conn, NOW)
    assert [e.event.name for e in events] == ["High", "Low"]
    assert events[0].event.start == datetime(2026, 11, 7, 20, tzinfo=NY)


def test_subscriber_round_trip(conn):
    new_id = db.save_subscriber(conn, "Ana", "ana@x.test", "jazz", "short", ["Mon", "Fri"])
    saved = db.get_subscriber(conn, new_id)
    assert (saved.name, saved.days) == ("Ana", ["Mon", "Fri"])


def test_each_subscriber_gets_a_long_random_link(conn):
    first = db.subscriber_token(conn, db.save_subscriber(conn, "Ana", "a@x.test", "jazz", "", ["Fri"]))
    second = db.subscriber_token(conn, db.save_subscriber(conn, "Bo", "b@x.test", "jazz", "", ["Fri"]))
    assert first != second and len(first) >= 32
    assert db.find_subscriber_by_token(conn, first).name == "Ana"
    assert db.find_subscriber_by_token(conn, "guess") is None


def test_older_database_gets_links_for_saved_people(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE subscribers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL,"
        " interests TEXT NOT NULL, look_prompt TEXT NOT NULL, days TEXT NOT NULL)"
    )
    old.execute("INSERT INTO subscribers VALUES (1, 'Ana', 'a@x.test', 'jazz', '', '[\"Fri\"]')")
    old.commit()
    old.close()
    conn = db.connect(str(path))
    token = db.subscriber_token(conn, 1)
    assert len(token) >= 32
    assert db.find_subscriber_by_token(conn, token).name == "Ana"
    conn.close()


def test_sent_ids_round_trip(conn):
    db.mark_sent(conn, 1, [2, 3])
    assert db.sent_event_ids(conn, 1) == {2, 3}


def test_ai_requests_counted_for_today_only(conn):
    db.record_ai_request(conn, NOW - timedelta(days=2))
    db.record_ai_request(conn, NOW)
    assert db.count_ai_requests_today(conn, NOW) == 1


def test_dropped_event_logged_with_reason(conn):
    db.log_dropped(conn, make_event(name="Bad"), "page did not open")
    assert db.dropped_reasons(conn) == {"page did not open": 1}


def test_saving_same_event_twice_keeps_one_row_and_id(conn):
    first = db.save_event(conn, make_event(), Score(10, True, None))
    second = db.save_event(conn, make_event(), Score(40, True, None))
    assert first == second
    assert [e.score.total for e in db.eligible_events(conn, NOW)] == [40]


def test_paste_round_trip(conn):
    db.save_paste(conn, "https://www.tiktok.com/@a/1", "great night", make_event(source_type="pasted"))
    (link, caption, event), = db.list_pastes(conn)
    assert (link, caption, event.name, event.source_type) == ("https://www.tiktok.com/@a/1", "great night", "Show", "pasted")
