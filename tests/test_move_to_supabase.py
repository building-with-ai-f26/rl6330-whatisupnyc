import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from app import db
from app.models import Score
from scripts.move_to_supabase import move
from tests.conftest import NY, make_event

OLD_SCHEMA = (Path(__file__).parent / "fixtures" / "old_sqlite_schema.sql").read_text()
BEFORE_ALL = datetime(2026, 1, 1, tzinfo=NY)


def old_db(tmp_path, *statements: str) -> str:
    """A SQLite file shaped like the app's database before the move, holding the given rows."""
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as old:
        old.executescript(OLD_SCHEMA)
        for statement in statements:
            old.execute(statement)
    return str(path)


def event_row(event_id: int, score: int, name: str = "Show", start: str = "2026-11-27T20:00:00-05:00") -> str:
    return (
        "INSERT INTO events (id, dedupe_key, name, start, venue, city, url, source, source_type, organizer,"
        " category, score, eligible, score_reason) VALUES"
        f" ({event_id}, 'key-{event_id}', '{name}', '{start}', 'Beacon Theatre', 'New York',"
        f" 'https://reliable.example/e/{event_id}', 'test', 'web', NULL, NULL, {score}, 1, NULL)"
    )


def subscriber_row(subscriber_id: int, token: str = "NULL", days: str = '["Friday"]') -> str:
    return (
        "INSERT INTO subscribers (id, name, email, interests, look_prompt, days, token) VALUES"
        f" ({subscriber_id}, 'Ana', 'ana@x.test', 'jazz', '', '{days}', {token})"
    )


def test_move_copies_every_table_and_keeps_ids(conn, tmp_path):
    old = old_db(
        tmp_path,
        event_row(7, 10),
        event_row(9, 40),
        "INSERT INTO dropped_events (name, url, reason) VALUES ('Bad', 'https://x.example', 'page did not open')",
        subscriber_row(3, token="'a-token-that-is-long-enough-to-be-a-real-one-123'"),
        "INSERT INTO sent_events (subscriber_id, event_id) VALUES (3, 9)",
        "INSERT INTO pastes (link, caption, name, start, venue, city, url, source, organizer) VALUES"
        " ('https://www.tiktok.com/@a/1', 'great night', 'Show', '2026-11-27T20:00:00-05:00', 'Beacon Theatre',"
        " 'New York', 'https://reliable.example/e/1', 'test', NULL)",
        "INSERT INTO ai_requests (day) VALUES ('2026-11-20')",
        "INSERT INTO waiting_pastes (link, caption, tries) VALUES ('https://www.tiktok.com/@a/2', '', 1)",
    )
    counts = move(old, conn)
    assert counts == {
        "events": 2, "dropped_events": 1, "subscribers": 1, "sent_events": 1,
        "pastes": 1, "ai_requests": 1, "waiting_pastes": 1,
    }
    assert db.sent_event_ids(conn, 3) == {9}
    assert [e.id for e in db.eligible_events(conn, BEFORE_ALL)] == [9, 7]
    assert db.find_subscriber_by_token(conn, "a-token-that-is-long-enough-to-be-a-real-one-123").id == 3
    assert db.count_ai_requests_today(conn, datetime(2026, 11, 20, 12, tzinfo=NY)) == 1


def test_move_converts_old_values(conn, tmp_path):
    old = old_db(tmp_path, event_row(1, 10), subscriber_row(1, days='["Friday", "Sunday"]'))
    move(old, conn)
    assert db.get_subscriber(conn, 1).days == ["Friday", "Sunday"]
    assert len(db.subscriber_token(conn, 1)) >= 43
    (event,) = db.eligible_events(conn, BEFORE_ALL)
    assert event.event.start == datetime(2026, 11, 27, 20, tzinfo=NY)
    assert event.score.eligible is True


def test_new_rows_after_move_get_fresh_ids(conn, tmp_path):
    move(old_db(tmp_path, event_row(7, 10), subscriber_row(3)), conn)
    assert db.save_event(conn, make_event(name="New"), Score(10, True, None)) > 7
    assert db.save_subscriber(conn, "Bo", "b@x.test", "jazz", "", ["Friday"]) > 3


def test_move_refuses_when_target_has_rows(conn, tmp_path):
    db.save_waiting_paste(conn, "https://www.tiktok.com/@a/1", "")
    with pytest.raises(RuntimeError, match="not empty"):
        move(old_db(tmp_path, event_row(7, 10)), conn)
    assert db.count_waiting_pastes(conn) == 1
    assert db.eligible_events(conn, BEFORE_ALL) == []
