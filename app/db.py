import json
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.checks import dedupe_key
from app.models import CandidateEvent, Pick, SavedIssue, Score, ScoredEvent, Subscriber

NY = ZoneInfo("America/New_York")

# The schema lives in supabase/migrations/. Every table in it, so tests can empty them.
TABLES = (
    "events", "dropped_events", "profiles", "sent_events", "newsletter_issues", "pastes", "ai_requests",
    "waiting_pastes",
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


def connect(url: str) -> Database:
    return ConnectionPool(
        url,
        min_size=1,
        max_size=4,
        open=True,
        # Checks a connection before lending it, so one Supabase closed overnight is replaced.
        check=ConnectionPool.check_connection,
        # Supabase's pooler doesn't support prepared statements.
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
    )


def _new_token() -> str:
    # 32 random bytes, so a link can't be guessed or counted up to.
    return secrets.token_urlsafe(32)


def save_event(conn: Database, event: CandidateEvent, score: Score) -> int:
    with conn.connection() as c:
        return c.execute(
            "INSERT INTO events (dedupe_key, name, start, venue, city, url, source, source_type, organizer,"
            " category, score, eligible, score_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (dedupe_key) DO UPDATE SET url = excluded.url, source = excluded.source,"
            " source_type = excluded.source_type, score = excluded.score, eligible = excluded.eligible,"
            " score_reason = excluded.score_reason RETURNING id",
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
            ),
        ).fetchone()["id"]


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
    return [e for e in events if e.event.start > now]


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
    with conn.connection() as c, c.transaction():
        c.execute("set local role authenticated")
        claims = json.dumps({"sub": user_id, "role": "authenticated"})
        c.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
        yield c


def create_profile(
    conn: Database, user_id: str, name: str, interests: str, look_prompt: str, days: list[str], now: datetime
) -> str | None:
    """Saves a new reader's preferences. Returns their first-look token, or None if they already have a profile."""
    token = _new_token()
    with conn.connection() as c:
        row = c.execute(
            "INSERT INTO profiles (user_id, name, interests, look_prompt, days, first_look_token, first_look_until)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING RETURNING user_id",
            (user_id, name, interests, look_prompt, days, token, now + FIRST_LOOK),
        ).fetchone()
    return token if row else None


def get_profile(conn: Database, user_id: str) -> Subscriber | None:
    with as_reader(conn, user_id) as c:
        row = c.execute("SELECT * FROM profiles WHERE user_id = %s", (user_id,)).fetchone()
    return _row_to_subscriber(row) if row else None


def profile_for_first_look(conn: Database, token: str, now: datetime) -> Subscriber | None:
    with conn.connection() as c:
        row = c.execute(
            "SELECT * FROM profiles WHERE first_look_token = %s AND first_look_until > %s", (token, now)
        ).fetchone()
    return _row_to_subscriber(row) if row else None


def end_first_look(conn: Database, user_id: str) -> None:
    """Stops a just-signed-up preview, once the account's owner has used an emailed link."""
    with conn.connection() as c:
        c.execute(
            "UPDATE profiles SET first_look_token = NULL, first_look_until = NULL WHERE user_id = %s", (user_id,)
        )


def _row_to_subscriber(row: dict) -> Subscriber:
    return Subscriber(
        str(row["user_id"]), row["name"], row["interests"], row["look_prompt"], row["days"],
        row.get("preferences_changed_at"),
    )


def update_profile(
    conn: Database, user_id: str, name: str, interests: str, look_prompt: str, days: list[str], now: datetime
) -> bool:
    """Saves a reader's edited preferences, as that reader. Returns False if they have no profile."""
    with as_reader(conn, user_id) as c:
        return c.execute(
            "UPDATE profiles SET name = %s, interests = %s, look_prompt = %s, days = %s, preferences_changed_at = %s"
            " WHERE user_id = %s",
            (name, interests, look_prompt, days, now, user_id),
        ).rowcount == 1


def save_issue(conn: Database, user_id: str, picks: list[Pick], now: datetime) -> None:
    """Saves a list the AI just made, and marks its events as sent, together.

    Written as the server's own role: readers can't write lists, so every saved pick came from code
    that confirmed it. user_id is always the signed-in reader Supabase verified.
    """
    saved = [{"event_id": pick.event.id, "reason": pick.reason} for pick in picks]
    with conn.connection() as c, c.transaction():
        c.execute(
            "INSERT INTO newsletter_issues (user_id, made_on, made_at, picks) VALUES (%s, %s, %s, %s)",
            (user_id, now.astimezone(NY).date(), now, Jsonb(saved)),
        )
        c.cursor().executemany(
            "INSERT INTO sent_events (user_id, event_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(user_id, pick.event.id) for pick in picks],
        )


def latest_issue(conn: Database, user_id: str) -> SavedIssue | None:
    with as_reader(conn, user_id) as c:
        row = c.execute(
            "SELECT made_on, made_at, picks FROM newsletter_issues WHERE user_id = %s ORDER BY made_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return SavedIssue(row["made_on"], row["made_at"], [(p["event_id"], p["reason"]) for p in row["picks"]])


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
