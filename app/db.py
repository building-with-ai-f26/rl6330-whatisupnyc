import os
import secrets
from datetime import date, datetime
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.checks import dedupe_key
from app.models import CandidateEvent, Score, ScoredEvent, Subscriber

NY = ZoneInfo("America/New_York")

# The schema lives in supabase/migrations/. Every table in it, for tests and the move script.
TABLES = ("events", "dropped_events", "subscribers", "sent_events", "pastes", "ai_requests", "waiting_pastes")

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


def save_subscriber(
    conn: Database, name: str, email: str, interests: str, look_prompt: str, days: list[str]
) -> int:
    with conn.connection() as c:
        return c.execute(
            "INSERT INTO subscribers (name, email, interests, look_prompt, days, token)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (name, email, interests, look_prompt, days, _new_token()),
        ).fetchone()["id"]


def get_subscriber(conn: Database, subscriber_id: int) -> Subscriber | None:
    with conn.connection() as c:
        row = c.execute("SELECT * FROM subscribers WHERE id = %s", (subscriber_id,)).fetchone()
    return _row_to_subscriber(row) if row else None


def subscriber_token(conn: Database, subscriber_id: int) -> str:
    with conn.connection() as c:
        return c.execute("SELECT token FROM subscribers WHERE id = %s", (subscriber_id,)).fetchone()["token"]


def find_subscriber_by_token(conn: Database, token: str) -> Subscriber | None:
    with conn.connection() as c:
        row = c.execute("SELECT * FROM subscribers WHERE token = %s", (token,)).fetchone()
    return _row_to_subscriber(row) if row else None


def _row_to_subscriber(row: dict) -> Subscriber:
    return Subscriber(row["id"], row["name"], row["email"], row["interests"], row["look_prompt"], row["days"])


def mark_sent(conn: Database, subscriber_id: int, event_ids: list[int]) -> None:
    with conn.connection() as c:
        c.cursor().executemany(
            "INSERT INTO sent_events (subscriber_id, event_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(subscriber_id, event_id) for event_id in event_ids],
        )


def sent_event_ids(conn: Database, subscriber_id: int) -> set[int]:
    with conn.connection() as c:
        rows = c.execute("SELECT event_id FROM sent_events WHERE subscriber_id = %s", (subscriber_id,)).fetchall()
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
