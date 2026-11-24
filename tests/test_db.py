from datetime import date, datetime, timedelta

import psycopg
import pytest

from app import db
from app.models import CandidateEvent, Pick, Score
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
    "delete from profiles where user_id = %s",
])
def test_reader_cannot_add_or_delete_profiles(conn, statement):
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
    # Supabase can close a connection, even one used moments ago; the next request must still work.
    db.save_waiting_paste(conn, "https://www.tiktok.com/@a/1", "")
    with psycopg.connect(TEST_URL, autocommit=True) as killer:
        # Waits until each connection is really closed, as one closed overnight would be.
        killer.execute(
            "select pg_terminate_backend(pid, 5000) from pg_stat_activity"
            " where datname = current_database() and pid <> pg_backend_pid()"
        )
    assert db.count_waiting_pastes(conn) == 1
    assert db.count_waiting_pastes(conn) == 1


def test_end_first_look_stops_the_token(conn):
    a = make_user(conn)
    token = db.create_profile(conn, a, "Ana", "jazz", "", ["Friday"], NOW)
    db.end_first_look(conn, a)
    assert db.profile_for_first_look(conn, token, NOW) is None
    assert db.get_profile(conn, a).name == "Ana"


def reader(conn) -> str:
    user = make_user(conn)
    db.create_profile(conn, user, "Ana", "jazz", "", ["Friday"], NOW)
    return user


def picks_for(conn, *names_and_reasons) -> list[Pick]:
    events = []
    for name, reason in names_and_reasons:
        event_id = db.save_event(conn, make_event(name=name, url=f"https://reliable.example/{name}"), Score(40, True))
        events.append(Pick(db.events_by_ids(conn, [event_id])[event_id], reason))
    return events


def test_saved_issue_round_trip_and_marks_events_sent(conn):
    a = reader(conn)
    picks = picks_for(conn, ("Jazz", "a"), ("Comedy", "b"))
    db.save_issue(conn, a, picks, datetime(2026, 11, 23, 20, tzinfo=NY))
    issue = db.latest_issue(conn, a)
    assert issue.made_on == date(2026, 11, 23)
    assert [(p.event_id, p.reason) for p in issue.picks] == [(picks[0].event.id, "a"), (picks[1].event.id, "b")]
    assert db.sent_event_ids(conn, a) == {p.event.id for p in picks}


def test_latest_issue_is_the_newest(conn):
    a = reader(conn)
    first, second = picks_for(conn, ("One", "x")), picks_for(conn, ("Two", "y"))
    db.save_issue(conn, a, first, NOW)
    db.save_issue(conn, a, second, NOW + timedelta(days=1))
    assert [(p.event_id, p.reason) for p in db.latest_issue(conn, a).picks] == [(second[0].event.id, "y")]
    assert db.latest_issue(conn, make_user(conn)) is None


def test_reader_cannot_read_another_readers_lists_or_add_any(conn):
    a, b = reader(conn), reader(conn)
    db.save_issue(conn, b, picks_for(conn, ("B's", "z")), NOW)
    with db.as_reader(conn, a) as c:
        assert c.execute("select * from newsletter_issues").fetchall() == []
    # Only the server writes lists, so a reader can't add one even to their own account.
    for owner in (a, b):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with db.as_reader(conn, a) as c:
                c.execute(
                    "insert into newsletter_issues (user_id, made_on, made_at, picks) values (%s, %s, %s, '[]')",
                    (owner, NOW.date(), NOW),
                )
    assert db.latest_issue(conn, a) is None and db.latest_issue(conn, b) is not None


def test_reader_can_update_only_their_own_preferences(conn):
    a, b = reader(conn), reader(conn)
    assert db.update_profile(conn, a, "Ana B", "comedy", "short", ["Monday"], NOW)
    saved = db.get_profile(conn, a)
    assert (saved.name, saved.interests, saved.look_prompt, saved.days, saved.changed_at) == (
        "Ana B", "comedy", "short", ["Monday"], NOW
    )
    with db.as_reader(conn, a) as c:
        assert c.execute("update profiles set name = 'x' where user_id = %s", (b,)).rowcount == 0
    assert db.get_profile(conn, b).name == "Ana"


def test_reader_cannot_change_first_look_columns(conn):
    a = reader(conn)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.as_reader(conn, a) as c:
            c.execute("update profiles set first_look_token = 'x' where user_id = %s", (a,))


def test_events_by_ids_includes_ineligible_and_skips_missing(conn):
    good = db.save_event(conn, make_event(name="Good"), Score(40, True))
    weak = db.save_event(conn, make_event(name="Weak", url="https://reliable.example/w"), Score(0, False, "x"))
    found = db.events_by_ids(conn, [good, weak, 99999])
    assert sorted(found) == sorted([good, weak])
    assert found[weak].event.name == "Weak"


def test_old_reader_tables_are_gone(conn):
    with conn.connection() as c:
        assert c.execute("select to_regclass('old_subscribers') as t").fetchone()["t"] is None
        assert c.execute("select to_regclass('old_sent_events') as t").fetchone()["t"] is None


def listed(conn, user, *names):
    """Events put on the reader's list, as the AI would; returns their IDs in order."""
    picks = picks_for(conn, *[(name, "fits") for name in names])
    db.save_issue(conn, user, picks, NOW)
    return [p.event.id for p in picks]


def test_history_is_every_event_on_the_readers_lists(conn):
    a, b = reader(conn), reader(conn)
    first = listed(conn, a, "One", "Two")
    second = listed(conn, a, "Three")
    listed(conn, b, "Bs")
    assert db.history_event_ids(conn, a) == set(first + second)


def test_save_for_later_round_trip_newest_first(conn):
    a = reader(conn)
    one, two = listed(conn, a, "One", "Two")
    db.save_for_later(conn, a, one, NOW)
    db.save_for_later(conn, a, two, NOW + timedelta(minutes=1))
    db.save_for_later(conn, a, one, NOW + timedelta(minutes=2))  # saving twice keeps one row
    assert db.saved_for_later_ids(conn, a) == [one, two]
    db.unsave(conn, a, one)
    assert db.saved_for_later_ids(conn, a) == [two]


def test_rating_round_trip_and_change(conn):
    a = reader(conn)
    (one,) = listed(conn, a, "One")
    db.rate_event(conn, a, one, 2, NOW)
    db.rate_event(conn, a, one, 5, NOW)
    assert db.ratings(conn, a) == {one: 5}


def test_ratings_must_be_one_to_five(conn):
    a = reader(conn)
    (one,) = listed(conn, a, "One")
    for bad in (0, 6):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.rate_event(conn, a, one, bad, NOW)


def test_unknown_events_cannot_be_saved_or_rated(conn):
    a = reader(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.save_for_later(conn, a, 999999, NOW)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.rate_event(conn, a, 999999, 4, NOW)


def test_readers_cannot_see_or_change_each_others_saves_and_ratings(conn):
    a, b = reader(conn), reader(conn)
    (one,) = listed(conn, b, "One")
    db.save_for_later(conn, b, one, NOW)
    db.rate_event(conn, b, one, 4, NOW)
    assert db.saved_for_later_ids(conn, a) == [] and db.ratings(conn, a) == {}
    with db.as_reader(conn, a) as c:
        assert c.execute("select * from saved_for_later").fetchall() == []
        assert c.execute("select * from event_ratings").fetchall() == []
        assert c.execute("delete from saved_for_later where user_id = %s", (b,)).rowcount == 0
    # Only the server writes saves and ratings, after its checks; a reader can't, even for themselves.
    for owner in (a, b):
        for statement in (
            "insert into saved_for_later (user_id, event_id, saved_at) values (%s, %s, now())",
            "insert into event_ratings (user_id, event_id, rating, rated_at) values (%s, %s, 1, now())",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with db.as_reader(conn, a) as c:
                    c.execute(statement, (owner, one))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.as_reader(conn, b) as c:
            c.execute("update event_ratings set rating = 1 where user_id = %s", (b,))
    assert db.ratings(conn, b) == {one: 4} and db.saved_for_later_ids(conn, b) == [one]


def test_reader_can_still_remove_their_own_saves_and_ratings(conn):
    a = reader(conn)
    (one,) = listed(conn, a, "One")
    db.save_for_later(conn, a, one, NOW)
    db.rate_event(conn, a, one, 3, NOW)
    with db.as_reader(conn, a) as c:
        assert c.execute("delete from saved_for_later where user_id = %s", (a,)).rowcount == 1
        assert c.execute("delete from event_ratings where user_id = %s", (a,)).rowcount == 1


def test_dashboard_data_in_one_lookup(conn):
    a = reader(conn)
    one, two = listed(conn, a, "One", "Two")
    db.save_for_later(conn, a, two, NOW)
    db.rate_event(conn, a, one, 4, NOW)
    saved, rated, history = db.dashboard_data(conn, a)
    assert (saved, rated, history) == ([two], {one: 4}, {one, two})
