from datetime import datetime, timedelta

import psycopg
import pytest

from app import db
from app.models import CandidateEvent, Score
from tests.conftest import NY, TEST_URL, make_event

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


def make_user(conn) -> str:
    """An account in the stand-in auth.users table, as Supabase Auth would create it."""
    with conn.connection() as c:
        return str(c.execute("insert into auth.users default values returning id").fetchone()["id"])


def test_profile_round_trip(conn):
    user = make_user(conn)
    db.create_profile(conn, user, "Ana", "jazz", "short", ["Monday", "Friday"], NOW)
    saved = db.get_profile(conn, user)
    assert (saved.id, saved.name, saved.interests, saved.look_prompt, saved.days) == (
        user, "Ana", "jazz", "short", ["Monday", "Friday"]
    )


def test_reader_sees_only_their_own_profile(conn):
    a, b = make_user(conn), make_user(conn)
    db.create_profile(conn, a, "Ana", "jazz", "", ["Friday"], NOW)
    db.create_profile(conn, b, "Bo", "comedy", "", ["Monday"], NOW)
    with db.as_reader(conn, a) as c:
        assert [r["name"] for r in c.execute("select name from profiles")] == ["Ana"]
        assert c.execute("select 1 from profiles where user_id = %s", (b,)).fetchone() is None
    assert db.get_profile(conn, b).name == "Bo"


def test_reader_cannot_read_or_add_another_readers_sent_events(conn):
    a, b = make_user(conn), make_user(conn)
    for user in (a, b):
        db.create_profile(conn, user, "R", "jazz", "", ["Friday"], NOW)
    db.mark_sent(conn, b, [7])
    assert db.sent_event_ids(conn, a) == set()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.as_reader(conn, a) as c:
            c.execute("insert into sent_events (user_id, event_id) values (%s, 8)", (b,))
    assert db.sent_event_ids(conn, b) == {7}


@pytest.mark.parametrize("statement", [
    "insert into profiles (user_id, name, interests, look_prompt, days) values (%s, 'x', 'x', '', '{}')",
    "update profiles set name = 'x' where user_id = %s",
    "delete from profiles where user_id = %s",
])
def test_reader_cannot_add_change_or_delete_profiles(conn, statement):
    a = make_user(conn)
    db.create_profile(conn, a, "Ana", "jazz", "", ["Friday"], NOW)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.as_reader(conn, a) as c:
            c.execute(statement, (a,))
    assert db.get_profile(conn, a).name == "Ana"


def test_create_profile_twice_returns_none_and_keeps_the_first(conn):
    a = make_user(conn)
    assert db.create_profile(conn, a, "Ana", "jazz", "", ["Friday"], NOW)
    assert db.create_profile(conn, a, "Eve", "taking over", "", ["Monday"], NOW) is None
    assert db.get_profile(conn, a).interests == "jazz"


def test_first_look_token_works_for_24_hours_only(conn):
    a = make_user(conn)
    token = db.create_profile(conn, a, "Ana", "jazz", "", ["Friday"], NOW)
    assert db.profile_for_first_look(conn, token, NOW + timedelta(hours=23)).id == a
    assert db.profile_for_first_look(conn, token, NOW + timedelta(hours=24)) is None
    assert db.profile_for_first_look(conn, "guess", NOW) is None


def test_first_look_token_is_long_and_random(conn):
    first = db.create_profile(conn, make_user(conn), "Ana", "jazz", "", ["Friday"], NOW)
    second = db.create_profile(conn, make_user(conn), "Bo", "jazz", "", ["Friday"], NOW)
    assert first != second and len(first) >= 43


def test_sent_ids_round_trip(conn):
    a = make_user(conn)
    db.create_profile(conn, a, "Ana", "jazz", "", ["Friday"], NOW)
    db.mark_sent(conn, a, [2, 3])
    db.mark_sent(conn, a, [3])
    assert db.sent_event_ids(conn, a) == {2, 3}


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


def test_every_table_has_row_level_security(conn):
    with conn.connection() as c:
        rows = c.execute(
            "select relname, relrowsecurity from pg_class"
            " where relnamespace = 'public'::regnamespace and relkind = 'r'"
        ).fetchall()
    assert {r["relname"] for r in rows} == set(db.TABLES)
    assert all(r["relrowsecurity"] for r in rows)


def test_waiting_pastes_listed_without_limit_by_default(conn):
    for n in range(3):
        db.save_waiting_paste(conn, f"https://www.tiktok.com/@a/{n}", "")
    assert len(db.list_waiting_pastes(conn)) == 3
    assert len(db.list_waiting_pastes(conn, 2)) == 2


def test_pool_recovers_after_connection_is_killed(conn):
    # Supabase can close an idle connection; the next request must still work.
    db.save_waiting_paste(conn, "https://www.tiktok.com/@a/1", "")
    with psycopg.connect(TEST_URL, autocommit=True) as killer:
        killer.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity"
            " where datname = current_database() and pid <> pg_backend_pid()"
        )
    assert db.count_waiting_pastes(conn) == 1
    assert db.count_waiting_pastes(conn) == 1
