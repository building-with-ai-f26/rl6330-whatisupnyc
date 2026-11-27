import json
import os
import secrets
import select
import time
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg import Connection, sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.checks import dedupe_key
from app.models import CandidateEvent, Pick, SavedIssue, SavedPick, Score, ScoredEvent, Subscriber

NY = ZoneInfo("America/New_York")

# The schema lives in supabase/migrations/. Every table in it, so tests can empty them.
TABLES = (
    "events", "dropped_events", "profiles", "sent_events", "newsletter_issues", "saved_for_later",
    "event_ratings", "pastes", "ai_requests", "waiting_pastes", "intake_events", "reader_lists",
)
# How long a brand-new reader can see their list before confirming their email.
FIRST_LOOK = timedelta(hours=24)

# Each function borrows one connection from the pool for its query.
Database = ConnectionPool


def url_from_env() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set: copy the Supabase session pooler connection string into .env")
    return url


# The server is far from the database (about 0.26 s a trip), so a connection used this recently is
# lent without testing it first; one idle longer is tested, so one Supabase closed overnight is replaced.
CHECK_AFTER_IDLE_SECONDS = 30
_returned_at: "weakref.WeakKeyDictionary[Connection, float]" = weakref.WeakKeyDictionary()


def _note_returned(c: Connection) -> None:
    _returned_at[c] = time.monotonic()


def _server_said_something(c: Connection) -> bool:
    # A connection at rest has nothing to read. Something waiting means the server is closing it.
    try:
        readable, _, _ = select.select([c.fileno()], [], [], 0)
    except (OSError, ValueError):
        return True
    return bool(readable)


def _check_if_idle(c: Connection) -> None:
    idle = time.monotonic() - _returned_at.get(c, float("-inf"))
    if idle > CHECK_AFTER_IDLE_SECONDS or _server_said_something(c):
        ConnectionPool.check_connection(c)


def connect(url: str) -> Database:
    return ConnectionPool(
        url,
        min_size=1,
        max_size=4,
        open=True,
        check=_check_if_idle,
        reset=_note_returned,
        # Supabase's pooler doesn't support prepared statements. Keepalives notice a dropped
        # connection between requests.
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None,
                "keepalives": 1, "keepalives_idle": 60, "keepalives_interval": 10, "keepalives_count": 3},
    )


def _new_token() -> str:
    # 32 random bytes, so a link can't be guessed or counted up to.
    return secrets.token_urlsafe(32)


def save_event(conn: Database, event: CandidateEvent, score: Score) -> int:
    with conn.connection() as c:
        return c.execute(
            "INSERT INTO events (dedupe_key, name, start, venue, city, url, source, source_type, organizer,"
            " category, score, eligible, score_reason, image_url, ends_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (dedupe_key) DO UPDATE SET url = excluded.url, source = excluded.source,"
            " source_type = excluded.source_type, score = excluded.score, eligible = excluded.eligible,"
            " score_reason = excluded.score_reason, image_url = coalesce(excluded.image_url, events.image_url),"
            " ends_at = coalesce(excluded.ends_at, events.ends_at)"
            " RETURNING id",
            (
                dedupe_key(event),
                event.name,
                event.start,
                event.venue,
                event.city,
                event.url,
                event.source,
                event.source_type,
                event.organizer,
                event.category,
                score.total,
                score.eligible,
                score.reason,
                event.image_url,
                event.end,
            ),
        ).fetchone()["id"]


def fill_event_image(conn: Database, key: str, image_url: str) -> None:
    """Gives an already-saved event a picture, if it has none yet."""
    with conn.connection() as c:
        c.execute("UPDATE events SET image_url = %s WHERE dedupe_key = %s AND image_url IS NULL", (image_url, key))


def save_intake(conn: Database, items: list[tuple[str, CandidateEvent, str]], now: datetime) -> tuple[int, int]:
    """Stores events sent to /api/intake. Returns (accepted, updated): a post sent again replaces its details
    and waits for the next collection again."""
    accepted = 0
    with conn.connection() as c:
        for post_url, event, caption in items:
            row = c.execute(
                "INSERT INTO intake_events (post_url, name, start, ends_at, venue, official_url, image_url, caption,"
                " category, received_at, received_day) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (post_url) DO UPDATE SET name = excluded.name, start = excluded.start,"
                " ends_at = excluded.ends_at, venue = excluded.venue, official_url = excluded.official_url,"
                " image_url = excluded.image_url, caption = excluded.caption, category = excluded.category,"
                # A resend counts toward today's limit, like a new post.
                " received_at = excluded.received_at, received_day = excluded.received_day, checked_at = NULL"
                " RETURNING (xmax = 0) AS inserted",
                (post_url, event.name, event.start, event.end, event.venue, event.url or None, event.image_url,
                 caption, event.category, now, now.astimezone(NY).date()),
            ).fetchone()
            accepted += row["inserted"]
    return accepted, len(items) - accepted


def count_intake_today(conn: Database, now: datetime) -> int:
    with conn.connection() as c:
        return c.execute(
            "SELECT COUNT(*) AS n FROM intake_events WHERE received_day = %s", (now.astimezone(NY).date(),)
        ).fetchone()["n"]


def waiting_intake(conn: Database) -> list[tuple[int, CandidateEvent]]:
    """Events sent in and not yet handled by the morning collection."""
    with conn.connection() as c:
        rows = c.execute("SELECT * FROM intake_events WHERE checked_at IS NULL ORDER BY id").fetchall()
    return [
        (row["id"], CandidateEvent(
            name=row["name"], start=row["start"].astimezone(NY), venue=row["venue"], city="New York",
            url=row["official_url"] or "", source="intake", source_type="api", category=row["category"],
            image_url=row["image_url"], end=row["ends_at"].astimezone(NY) if row["ends_at"] else None,
        ))
        for row in rows
    ]


def mark_intake_checked(conn: Database, ids: list[int], now: datetime) -> None:
    # A post resent after this run read it stays waiting, so its new details are handled next time.
    with conn.connection() as c:
        c.execute("UPDATE intake_events SET checked_at = %s WHERE id = ANY(%s) AND received_at <= %s", (now, ids, now))


def retire_muse_event(conn: Database, intake_id: int) -> None:
    """Takes the event a Muse post was saved as off future lists, because a corrected copy replaces it.
    Only while Muse still owns that event and no other post points to it. Readers' saves and ratings stay."""
    with conn.connection() as c:
        c.execute(
            "UPDATE events e SET eligible = false FROM intake_events i"
            " WHERE i.id = %s AND e.id = i.event_id AND e.source = 'intake'"
            " AND NOT EXISTS (SELECT 1 FROM intake_events j WHERE j.event_id = e.id AND j.id <> i.id)",
            (intake_id,),
        )
        c.execute("UPDATE intake_events SET event_id = NULL WHERE id = %s", (intake_id,))


def link_intake(conn: Database, intake_id: int, event_id: int) -> None:
    with conn.connection() as c:
        c.execute("UPDATE intake_events SET event_id = %s WHERE id = %s", (event_id, intake_id))


def event_source_by_key(conn: Database, key: str) -> tuple[str, bool] | None:
    """(source, eligible) of the saved event with this duplicate key, if any."""
    with conn.connection() as c:
        row = c.execute("SELECT source, eligible FROM events WHERE dedupe_key = %s", (key,)).fetchone()
    return (row["source"], row["eligible"]) if row else None


def fill_event_image_by_id(conn: Database, event_id: int, image_url: str) -> None:
    with conn.connection() as c:
        c.execute("UPDATE events SET image_url = %s WHERE id = %s AND image_url IS NULL", (image_url, event_id))


def saved_event_keys(conn: Database) -> dict[str, bool]:
    with conn.connection() as c:
        rows = c.execute("SELECT dedupe_key, eligible FROM events").fetchall()
    return {row["dedupe_key"]: row["eligible"] for row in rows}


def save_paste(conn: Database, link: str, caption: str, event: CandidateEvent) -> None:
    with conn.connection() as c:
        c.execute(
            "INSERT INTO pastes (link, caption, name, start, venue, city, url, source, organizer)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (link, caption, event.name, event.start, event.venue, event.city, event.url,
             event.source, event.organizer),
        )


def save_waiting_paste(conn: Database, link: str, caption: str) -> None:
    # A pasted post waits here until the next collection run checks it.
    with conn.connection() as c:
        c.execute("INSERT INTO waiting_pastes (link, caption) VALUES (%s, %s)", (link, caption))


def count_waiting_pastes(conn: Database) -> int:
    with conn.connection() as c:
        return c.execute("SELECT COUNT(*) AS n FROM waiting_pastes").fetchone()["n"]


def list_waiting_pastes(conn: Database, limit: int | None = None) -> list[tuple[int, str, str]]:
    # LIMIT NULL means no limit.
    with conn.connection() as c:
        rows = c.execute("SELECT id, link, caption FROM waiting_pastes ORDER BY id LIMIT %s", (limit,)).fetchall()
    return [(row["id"], row["link"], row["caption"]) for row in rows]


def count_waiting_paste_try(conn: Database, paste_id: int, max_tries: int) -> None:
    """Note one failed try, and give up on the post after max_tries."""
    with conn.connection() as c:
        c.execute("UPDATE waiting_pastes SET tries = tries + 1 WHERE id = %s", (paste_id,))
        c.execute("DELETE FROM waiting_pastes WHERE id = %s AND tries >= %s", (paste_id, max_tries))


def delete_waiting_paste(conn: Database, paste_id: int) -> None:
    with conn.connection() as c:
        c.execute("DELETE FROM waiting_pastes WHERE id = %s", (paste_id,))


def list_pastes(conn: Database) -> list[tuple[str, str, CandidateEvent]]:
    with conn.connection() as c:
        rows = c.execute("SELECT * FROM pastes ORDER BY id").fetchall()
    return [
        (
            row["link"],
            row["caption"],
            CandidateEvent(
                name=row["name"],
                start=row["start"].astimezone(NY),
                venue=row["venue"],
                city=row["city"],
                url=row["url"],
                source=row["source"],
                source_type="pasted",
                organizer=row["organizer"],
            ),
        )
        for row in rows
    ]


def eligible_events(conn: Database, now: datetime) -> list[ScoredEvent]:
    with conn.connection() as c:
        rows = c.execute("SELECT * FROM events WHERE eligible ORDER BY score DESC, id").fetchall()
    events = [_row_to_scored(row) for row in rows]
    return [e for e in events if not e.event.is_over(now)]


def _row_to_scored(row: dict) -> ScoredEvent:
    event = CandidateEvent(
        name=row["name"],
        # Postgres hands times back in UTC; the app works in New York time.
        start=row["start"].astimezone(NY),
        venue=row["venue"],
        city=row["city"],
        url=row["url"],
        source=row["source"],
        source_type=row["source_type"],
        organizer=row["organizer"],
        category=row["category"],
        image_url=row.get("image_url"),
        end=row["ends_at"].astimezone(NY) if row.get("ends_at") else None,
    )
    return ScoredEvent(row["id"], event, Score(row["score"], row["eligible"], row["score_reason"]))


def log_dropped(conn: Database, event: CandidateEvent, reason: str) -> None:
    with conn.connection() as c:
        c.execute(
            "INSERT INTO dropped_events (name, url, reason) VALUES (%s, %s, %s)",
            (event.name, event.url, reason),
        )


def dropped_reasons(conn: Database) -> dict[str, int]:
    with conn.connection() as c:
        rows = c.execute("SELECT reason, COUNT(*) AS n FROM dropped_events GROUP BY reason").fetchall()
    return {row["reason"]: row["n"] for row in rows}


@contextmanager
def as_reader(conn: Database, user_id: str) -> Iterator[Connection]:
    """A connection limited to one reader's rows by the database's own policies.

    Inside one transaction it switches to Supabase's "authenticated" role and sets the reader's
    user ID where auth.uid() reads it. Both reset when the transaction ends.
    """
    claims = json.dumps({"sub": user_id, "role": "authenticated"})
    with conn.connection() as c:
        # BEGIN and both settings in one round trip; both settings end with the transaction (the true).
        c.execute(sql.SQL(
            "BEGIN; select set_config('role', 'authenticated', true), set_config('request.jwt.claims', {}, true)"
        ).format(sql.Literal(claims)))
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        c.execute("COMMIT")


def create_profile(
    conn: Database, user_id: str, name: str, interests: str, look_prompt: str, days: list[str], now: datetime,
    send_hour: int = 8,
) -> str | None:
    """Saves a new reader and their first list. Returns their first-look token, or None if they already have
    a profile."""
    token = _new_token()
    with conn.connection() as c, c.transaction():
        row = c.execute(
            "INSERT INTO profiles (user_id, name, interests, look_prompt, days, send_hour, first_look_token,"
            " first_look_until) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING"
            " RETURNING user_id",
            (user_id, name, interests, look_prompt, days, send_hour, token, now + FIRST_LOOK),
        ).fetchone()
        # Also gives back a list to a reader left with none.
        c.execute(
            "INSERT INTO reader_lists (user_id, title, interests, days, send_hour)"
            " SELECT %(u)s, %(t)s, %(i)s, %(d)s, %(h)s"
            " WHERE NOT EXISTS (SELECT 1 FROM reader_lists WHERE user_id = %(u)s)",
            {"u": user_id, "t": FIRST_LIST_TITLE, "i": interests, "d": days, "h": send_hour},
        )
    return token if row else None


# Each reader can keep this many lists.
MAX_LISTS = 3
FIRST_LIST_TITLE = "My list"
# The reader's list with ID %(l)s, or their first list when %(l)s is missing or isn't theirs.
_CHOSEN_LIST = (
    "(SELECT id FROM reader_lists WHERE user_id = %(u)s ORDER BY (id = %(l)s) DESC NULLS LAST, id LIMIT 1)"
)
# For writes: exactly the list with ID %(l)s (their first when %(l)s is missing), or nothing at all, so a change
# meant for a deleted list never lands on another one.
_EXACT_LIST = (
    "(SELECT id FROM reader_lists WHERE user_id = %(u)s"
    " AND id = coalesce(%(l)s, (SELECT min(id) FROM reader_lists WHERE user_id = %(u)s)))"
)
# One reader's preferences for one list, with the titles of all their lists, in one query.
_PROFILE = (
    "SELECT p.user_id, p.name, p.look_prompt, l.id AS list_id, l.title, l.interests, l.days, l.changed_at,"
    " (SELECT json_agg(json_build_array(o.id, o.title) ORDER BY o.id) FROM reader_lists o"
    "   WHERE o.user_id = p.user_id) AS lists"
    " FROM profiles p JOIN reader_lists l ON l.user_id = p.user_id"
)


def get_profile(conn: Database, user_id: str, list_id: int | None = None) -> Subscriber | None:
    """The reader's preferences for one of their lists (their first when list_id is missing or not theirs)."""
    with as_reader(conn, user_id) as c:
        row = c.execute(
            _PROFILE + " WHERE p.user_id = %(u)s ORDER BY (l.id = %(l)s) DESC NULLS LAST, l.id LIMIT 1",
            {"u": user_id, "l": list_id},
        ).fetchone()
    return _row_to_subscriber(row) if row else None


def profile_for_first_look(conn: Database, token: str, now: datetime) -> Subscriber | None:
    with conn.connection() as c:
        row = c.execute(
            _PROFILE + " WHERE p.first_look_token = %(t)s AND p.first_look_until > %(n)s ORDER BY l.id LIMIT 1",
            {"t": token, "n": now},
        ).fetchone()
    return _row_to_subscriber(row) if row else None


def reader_lists(conn: Database, user_id: str) -> list[tuple[int, str]]:
    with as_reader(conn, user_id) as c:
        rows = c.execute("SELECT id, title FROM reader_lists WHERE user_id = %s ORDER BY id", (user_id,)).fetchall()
    return [(row["id"], row["title"]) for row in rows]


def add_list(conn: Database, user_id: str, title: str, interests: str, days: list[str], send_hour: int,
             now: datetime) -> int | None:
    """A new list for the reader. None when they already have MAX_LISTS. Written as the server's role, for the
    signed-in reader Supabase verified."""
    with conn.connection() as c, c.transaction():
        # Locks the reader's row first, so two requests at once can't both count 2 and add a fourth list.
        c.execute("SELECT 1 FROM profiles WHERE user_id = %s FOR UPDATE", (user_id,))
        row = c.execute(
            "INSERT INTO reader_lists (user_id, title, interests, days, send_hour, changed_at)"
            " SELECT %(u)s, %(t)s, %(i)s, %(d)s, %(h)s, %(n)s"
            " WHERE (SELECT count(*) FROM reader_lists WHERE user_id = %(u)s) < %(max)s RETURNING id",
            {"u": user_id, "t": title, "i": interests, "d": days, "h": send_hour, "n": now, "max": MAX_LISTS},
        ).fetchone()
    return row["id"] if row else None


def delete_list(conn: Database, user_id: str, list_id: int) -> bool:
    """Deletes one of the reader's lists and its saved daily lists, never their last one."""
    with conn.connection() as c, c.transaction():
        # The same lock, so two deletes at once can't remove the reader's last list.
        c.execute("SELECT 1 FROM profiles WHERE user_id = %s FOR UPDATE", (user_id,))
        return c.execute(
            "DELETE FROM reader_lists WHERE id = %(l)s AND user_id = %(u)s"
            " AND (SELECT count(*) FROM reader_lists WHERE user_id = %(u)s) > 1",
            {"l": list_id, "u": user_id},
        ).rowcount == 1


def end_first_look(conn: Database, user_id: str) -> None:
    """Stops a just-signed-up preview, once the account's owner has used an emailed link."""
    with conn.connection() as c:
        c.execute(
            "UPDATE profiles SET first_look_token = NULL, first_look_until = NULL WHERE user_id = %s", (user_id,)
        )


def _row_to_subscriber(row: dict) -> Subscriber:
    return Subscriber(
        str(row["user_id"]), row["name"], row["interests"], row["look_prompt"], row["days"],
        row.get("changed_at"), row.get("list_id"), row.get("title") or "",
        [(item[0], item[1]) for item in row.get("lists") or []],
    )


def update_profile(
    conn: Database, user_id: str, name: str, interests: str, look_prompt: str, days: list[str], now: datetime,
    list_id: int | None = None, title: str | None = None,
) -> bool:
    """Saves a reader's name and one list's edited preferences, as that reader. False if that list is gone."""
    values = {"u": user_id, "l": list_id, "name": name, "i": interests, "d": days, "n": now, "t": title}
    with as_reader(conn, user_id) as c:
        changed = c.execute(
            "UPDATE reader_lists SET interests = %(i)s, days = %(d)s, changed_at = %(n)s,"
            " title = coalesce(%(t)s, title) WHERE id = " + _EXACT_LIST, values,
        ).rowcount == 1
        if changed:
            c.execute("UPDATE profiles SET name = %(name)s WHERE user_id = %(u)s", values)
    return changed


def email_settings(conn: Database, user_id: str, list_id: int | None = None) -> tuple[int, bool] | None:
    """(send_hour, email_on) for one of the reader's lists."""
    with as_reader(conn, user_id) as c:
        row = c.execute(
            "SELECT send_hour, email_on FROM reader_lists WHERE id = " + _CHOSEN_LIST, {"u": user_id, "l": list_id}
        ).fetchone()
    return (row["send_hour"], row["email_on"]) if row else None


def set_email_settings(conn: Database, user_id: str, send_hour: int, email_on: bool,
                       list_id: int | None = None) -> None:
    with as_reader(conn, user_id) as c:
        c.execute(
            "UPDATE reader_lists SET send_hour = %(h)s, email_on = %(on)s WHERE id = " + _EXACT_LIST,
            {"u": user_id, "l": list_id, "h": send_hour, "on": email_on},
        )


# Hourly tries a day for each reader: their hour and the next three. After that, not until their next day.
CATCH_UP_HOURS = 4


def readers_due_an_email(conn: Database, now: datetime) -> list[tuple[Subscriber, str]]:
    """Lists whose email should go out now, each with its reader's address: email on, a confirmed address,
    today one of the list's days, its hour reached in the last CATCH_UP_HOURS hours, not yet emailed today."""
    local = now.astimezone(NY)
    with conn.connection() as c:
        rows = c.execute(
            "SELECT p.user_id, p.name, p.look_prompt, l.id AS list_id, l.title, l.interests, l.days, l.changed_at,"
            " u.email FROM reader_lists l JOIN profiles p ON p.user_id = l.user_id JOIN auth.users u ON u.id = l.user_id"
            " WHERE l.email_on AND u.email_confirmed_at IS NOT NULL AND u.email IS NOT NULL"
            " AND u.deleted_at IS NULL AND (u.banned_until IS NULL OR u.banned_until < %s)"
            " AND %s = ANY(l.days) AND l.send_hour <= %s AND l.send_hour > %s - %s"
            " AND (l.emailed_on IS NULL OR l.emailed_on < %s)"
            " ORDER BY l.send_hour, l.id",
            (now, local.strftime("%A"), local.hour, local.hour, CATCH_UP_HOURS, local.date()),
        ).fetchall()
    return [(_row_to_subscriber(row), row["email"]) for row in rows]


def claim_daily_email(conn: Database, list_id: int, now: datetime) -> bool:
    """Marks a list's email for today as sent before sending it, so a crash or a second run never sends it
    twice. False if another run already claimed it."""
    today = now.astimezone(NY).date()
    with conn.connection() as c:
        return c.execute(
            "UPDATE reader_lists SET emailed_on = %s WHERE id = %s AND (emailed_on IS NULL OR emailed_on < %s)"
            " RETURNING id", (today, list_id, today),
        ).fetchone() is not None


def release_daily_email(conn: Database, list_id: int) -> None:
    """Undoes a claim when the email clearly wasn't sent, so the next hour tries again."""
    with conn.connection() as c:
        c.execute("UPDATE reader_lists SET emailed_on = NULL WHERE id = %s", (list_id,))


def save_issue(conn: Database, user_id: str, picks: list[Pick], now: datetime, list_id: int | None = None) -> None:
    """Saves a list the AI just made, and marks the events shown at once as sent, together.
    Picks behind "Show more" count as sent only when the reader reveals them (reveal_picks).

    Written as the server's own role: readers can't write lists, so every saved pick came from code
    that confirmed it. user_id is always the signed-in reader Supabase verified.
    """
    saved = [{"event_id": pick.event.id, "reason": pick.reason, "more_like": pick.more_like, "extra": pick.extra}
             for pick in picks]
    with conn.connection() as c, c.transaction():
        c.execute(
            "INSERT INTO newsletter_issues (user_id, list_id, made_on, made_at, picks)"
            " VALUES (%(u)s, " + _EXACT_LIST + ", %(day)s, %(at)s, %(picks)s)",
            {"u": user_id, "l": list_id, "day": now.astimezone(NY).date(), "at": now, "picks": Jsonb(saved)},
        )
        c.cursor().executemany(
            "INSERT INTO sent_events (user_id, event_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(user_id, pick.event.id) for pick in picks if not pick.extra],
        )


def latest_issue(conn: Database, user_id: str, list_id: int | None = None) -> SavedIssue | None:
    """The newest daily list saved for one of the reader's lists (their first when list_id is missing)."""
    with as_reader(conn, user_id) as c:
        row = c.execute(
            "SELECT made_on, made_at, picks FROM newsletter_issues WHERE list_id = " + _CHOSEN_LIST
            + " ORDER BY made_at DESC, id DESC LIMIT 1",
            {"u": user_id, "l": list_id},
        ).fetchone()
    if row is None:
        return None
    return SavedIssue(row["made_on"], row["made_at"], [
        SavedPick(p["event_id"], p["reason"], p.get("more_like", False), p.get("extra", False)) for p in row["picks"]
    ])


def history_event_ids(conn: Database, user_id: str) -> set[int]:
    """Every event that was on one of the reader's lists."""
    with as_reader(conn, user_id) as c:
        rows = c.execute(
            "SELECT DISTINCT (pick ->> 'event_id')::bigint AS event_id"
            " FROM newsletter_issues, jsonb_array_elements(picks) AS pick WHERE user_id = %s"
            # Picks still behind "Show more" were never shown to the reader.
            " AND NOT coalesce((pick ->> 'extra')::boolean, false)",
            (user_id,),
        ).fetchall()
    return {row["event_id"] for row in rows}


def save_for_later(conn: Database, user_id: str, event_id: int, now: datetime) -> None:
    """Written as the server's role, after the route checked the event was on this reader's lists."""
    with conn.connection() as c:
        c.execute(
            "INSERT INTO saved_for_later (user_id, event_id, saved_at) VALUES (%s, %s, %s)"
            " ON CONFLICT (user_id, event_id) DO UPDATE SET saved_at = excluded.saved_at",
            (user_id, event_id, now),
        )


def unsave(conn: Database, user_id: str, event_id: int) -> None:
    with as_reader(conn, user_id) as c:
        c.execute("DELETE FROM saved_for_later WHERE user_id = %s AND event_id = %s", (user_id, event_id))


def saved_for_later_ids(conn: Database, user_id: str) -> list[int]:
    """The reader's saved events, most recently saved first."""
    with as_reader(conn, user_id) as c:
        rows = c.execute(
            "SELECT event_id FROM saved_for_later WHERE user_id = %s ORDER BY saved_at DESC, event_id", (user_id,)
        ).fetchall()
    return [row["event_id"] for row in rows]


def rate_event(conn: Database, user_id: str, event_id: int, rating: int, now: datetime) -> None:
    """Written as the server's role, after the route checked the event was on this reader's lists
    and has happened."""
    with conn.connection() as c:
        c.execute(
            "INSERT INTO event_ratings (user_id, event_id, rating, rated_at) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (user_id, event_id) DO UPDATE SET rating = excluded.rating, rated_at = excluded.rated_at",
            (user_id, event_id, rating, now),
        )


def ratings(conn: Database, user_id: str) -> dict[int, int]:
    with as_reader(conn, user_id) as c:
        rows = c.execute("SELECT event_id, rating FROM event_ratings WHERE user_id = %s", (user_id,)).fetchall()
    return {row["event_id"]: row["rating"] for row in rows}


def dashboard_data(conn: Database, user_id: str) -> tuple[list[int], dict[int, int], set[int]]:
    """The reader's saved events (newest first), ratings and list history, in one lookup."""
    with as_reader(conn, user_id) as c:
        # One statement, so one round trip.
        row = c.execute(
            "SELECT"
            " (SELECT coalesce(json_agg(event_id ORDER BY saved_at DESC, event_id), '[]')"
            "  FROM saved_for_later WHERE user_id = %(u)s) AS saved,"
            " (SELECT coalesce(json_object_agg(event_id, rating), '{}') FROM event_ratings WHERE user_id = %(u)s) AS rated,"
            " (SELECT coalesce(json_agg(DISTINCT (pick ->> 'event_id')::bigint), '[]')"
            "  FROM newsletter_issues, jsonb_array_elements(picks) AS pick WHERE user_id = %(u)s"
            "  AND NOT coalesce((pick ->> 'extra')::boolean, false)) AS history",
            {"u": user_id},
        ).fetchone()
    return row["saved"], {int(k): v for k, v in row["rated"].items()}, set(row["history"])


def events_by_ids(conn: Database, ids: list[int]) -> dict[int, ScoredEvent]:
    """Events by ID, eligible or not. IDs with no event are left out."""
    with conn.connection() as c:
        rows = c.execute("SELECT * FROM events WHERE id = ANY(%s)", (list(ids),)).fetchall()
    return {row["id"]: _row_to_scored(row) for row in rows}


def mark_sent(conn: Database, user_id: str, event_ids: list[int]) -> None:
    with as_reader(conn, user_id) as c:
        c.cursor().executemany(
            "INSERT INTO sent_events (user_id, event_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(user_id, event_id) for event_id in event_ids],
        )


def reveal_picks(conn: Database, user_id: str, event_ids: list[int], list_id: int | None = None) -> None:
    """The reader tapped Show more: those picks stay shown on their latest list, and count as seen.
    Written as the server's role (readers can't write lists), after the route checked the IDs are
    on that list."""
    with conn.connection() as c, c.transaction():
        c.execute(
            "UPDATE newsletter_issues SET picks = ("
            "  SELECT jsonb_agg(CASE WHEN (p ->> 'event_id')::bigint = ANY(%(ids)s)"
            "                   THEN p || '{\"extra\": false}'::jsonb ELSE p END ORDER BY n)"
            "  FROM jsonb_array_elements(picks) WITH ORDINALITY AS t(p, n))"
            " WHERE id = (SELECT id FROM newsletter_issues WHERE list_id = " + _EXACT_LIST
            + " ORDER BY made_at DESC, id DESC LIMIT 1)",
            {"ids": event_ids, "u": user_id, "l": list_id},
        )
        c.cursor().executemany(
            "INSERT INTO sent_events (user_id, event_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(user_id, event_id) for event_id in event_ids],
        )


def sent_event_ids(conn: Database, user_id: str) -> set[int]:
    with as_reader(conn, user_id) as c:
        rows = c.execute("SELECT event_id FROM sent_events WHERE user_id = %s", (user_id,)).fetchall()
    return {row["event_id"] for row in rows}


def record_ai_request(conn: Database, now: datetime) -> None:
    with conn.connection() as c:
        c.execute("INSERT INTO ai_requests (day) VALUES (%s)", (_utc_day(now),))


def count_ai_requests_today(conn: Database, now: datetime) -> int:
    with conn.connection() as c:
        return c.execute("SELECT COUNT(*) AS n FROM ai_requests WHERE day = %s", (_utc_day(now),)).fetchone()["n"]


def _utc_day(now: datetime) -> date:
    # OpenRouter's daily limit resets on the UTC day.
    return now.astimezone(ZoneInfo("UTC")).date()
