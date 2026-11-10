import json
import secrets
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from app.checks import dedupe_key
from app.models import CandidateEvent, Score, ScoredEvent, Subscriber

NY = ZoneInfo("America/New_York")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    start TEXT NOT NULL,
    venue TEXT NOT NULL,
    city TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    organizer TEXT,
    category TEXT,
    score INTEGER NOT NULL,
    eligible INTEGER NOT NULL,
    score_reason TEXT
);
CREATE TABLE IF NOT EXISTS dropped_events (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscribers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    interests TEXT NOT NULL,
    look_prompt TEXT NOT NULL,
    days TEXT NOT NULL,
    token TEXT
);
CREATE TABLE IF NOT EXISTS sent_events (
    subscriber_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    PRIMARY KEY (subscriber_id, event_id)
);
CREATE TABLE IF NOT EXISTS pastes (
    id INTEGER PRIMARY KEY,
    link TEXT NOT NULL,
    caption TEXT NOT NULL,
    name TEXT NOT NULL,
    start TEXT NOT NULL,
    venue TEXT NOT NULL,
    city TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    organizer TEXT
);
CREATE TABLE IF NOT EXISTS ai_requests (
    day TEXT NOT NULL
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _give_subscribers_links(conn)
    return conn


def _new_token() -> str:
    # 32 random bytes, so a link can't be guessed or counted up to.
    return secrets.token_urlsafe(32)


def _give_subscribers_links(conn: sqlite3.Connection) -> None:
    # Databases made before private links have no token column yet.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(subscribers)")}
    if "token" not in columns:
        conn.execute("ALTER TABLE subscribers ADD COLUMN token TEXT")
    for row in conn.execute("SELECT id FROM subscribers WHERE token IS NULL").fetchall():
        conn.execute("UPDATE subscribers SET token = ? WHERE id = ?", (_new_token(), row["id"]))
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS subscribers_token ON subscribers (token)")
    conn.commit()


def save_event(conn: sqlite3.Connection, event: CandidateEvent, score: Score) -> int:
    key = dedupe_key(event)
    conn.execute(
        "INSERT INTO events (dedupe_key, name, start, venue, city, url, source, source_type, organizer,"
        " category, score, eligible, score_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(dedupe_key) DO UPDATE SET url = excluded.url, source = excluded.source,"
        " source_type = excluded.source_type, score = excluded.score, eligible = excluded.eligible,"
        " score_reason = excluded.score_reason",
        (
            key,
            event.name,
            event.start.isoformat(),
            event.venue,
            event.city,
            event.url,
            event.source,
            event.source_type,
            event.organizer,
            event.category,
            score.total,
            int(score.eligible),
            score.reason,
        ),
    )
    conn.commit()
    return conn.execute("SELECT id FROM events WHERE dedupe_key = ?", (key,)).fetchone()["id"]


def saved_event_keys(conn: sqlite3.Connection) -> dict[str, bool]:
    rows = conn.execute("SELECT dedupe_key, eligible FROM events").fetchall()
    return {row["dedupe_key"]: bool(row["eligible"]) for row in rows}


def save_paste(conn: sqlite3.Connection, link: str, caption: str, event: CandidateEvent) -> None:
    conn.execute(
        "INSERT INTO pastes (link, caption, name, start, venue, city, url, source, organizer)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (link, caption, event.name, event.start.isoformat(), event.venue, event.city, event.url,
         event.source, event.organizer),
    )
    conn.commit()


def list_pastes(conn: sqlite3.Connection) -> list[tuple[str, str, CandidateEvent]]:
    return [
        (
            row["link"],
            row["caption"],
            CandidateEvent(
                name=row["name"],
                start=datetime.fromisoformat(row["start"]),
                venue=row["venue"],
                city=row["city"],
                url=row["url"],
                source=row["source"],
                source_type="pasted",
                organizer=row["organizer"],
            ),
        )
        for row in conn.execute("SELECT * FROM pastes ORDER BY id").fetchall()
    ]


def eligible_events(conn: sqlite3.Connection, now: datetime) -> list[ScoredEvent]:
    rows = conn.execute(
        "SELECT * FROM events WHERE eligible = 1 ORDER BY score DESC, id"
    ).fetchall()
    events = [_row_to_scored(row) for row in rows]
    return [e for e in events if e.event.start > now]


def _row_to_scored(row: sqlite3.Row) -> ScoredEvent:
    event = CandidateEvent(
        name=row["name"],
        start=datetime.fromisoformat(row["start"]),
        venue=row["venue"],
        city=row["city"],
        url=row["url"],
        source=row["source"],
        source_type=row["source_type"],
        organizer=row["organizer"],
        category=row["category"],
    )
    return ScoredEvent(row["id"], event, Score(row["score"], bool(row["eligible"]), row["score_reason"]))


def log_dropped(conn: sqlite3.Connection, event: CandidateEvent, reason: str) -> None:
    conn.execute(
        "INSERT INTO dropped_events (name, url, reason) VALUES (?, ?, ?)",
        (event.name, event.url, reason),
    )
    conn.commit()


def dropped_reasons(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT reason, COUNT(*) AS n FROM dropped_events GROUP BY reason").fetchall()
    return {row["reason"]: row["n"] for row in rows}


def save_subscriber(
    conn: sqlite3.Connection, name: str, email: str, interests: str, look_prompt: str, days: list[str]
) -> int:
    cursor = conn.execute(
        "INSERT INTO subscribers (name, email, interests, look_prompt, days, token) VALUES (?, ?, ?, ?, ?, ?)",
        (name, email, interests, look_prompt, json.dumps(days), _new_token()),
    )
    conn.commit()
    return cursor.lastrowid


def get_subscriber(conn: sqlite3.Connection, subscriber_id: int) -> Subscriber | None:
    row = conn.execute("SELECT * FROM subscribers WHERE id = ?", (subscriber_id,)).fetchone()
    return _row_to_subscriber(row) if row else None


def subscriber_token(conn: sqlite3.Connection, subscriber_id: int) -> str:
    return conn.execute("SELECT token FROM subscribers WHERE id = ?", (subscriber_id,)).fetchone()["token"]


def find_subscriber_by_token(conn: sqlite3.Connection, token: str) -> Subscriber | None:
    row = conn.execute("SELECT * FROM subscribers WHERE token = ?", (token,)).fetchone()
    return _row_to_subscriber(row) if row else None


def _row_to_subscriber(row: sqlite3.Row) -> Subscriber:
    return Subscriber(
        row["id"], row["name"], row["email"], row["interests"], row["look_prompt"], json.loads(row["days"])
    )


def mark_sent(conn: sqlite3.Connection, subscriber_id: int, event_ids: list[int]) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO sent_events (subscriber_id, event_id) VALUES (?, ?)",
        [(subscriber_id, event_id) for event_id in event_ids],
    )
    conn.commit()


def sent_event_ids(conn: sqlite3.Connection, subscriber_id: int) -> set[int]:
    rows = conn.execute("SELECT event_id FROM sent_events WHERE subscriber_id = ?", (subscriber_id,))
    return {row["event_id"] for row in rows}


def record_ai_request(conn: sqlite3.Connection, now: datetime) -> None:
    conn.execute("INSERT INTO ai_requests (day) VALUES (?)", (_utc_day(now),))
    conn.commit()


def count_ai_requests_today(conn: sqlite3.Connection, now: datetime) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM ai_requests WHERE day = ?", (_utc_day(now),)).fetchone()
    return row["n"]


def _utc_day(now: datetime) -> str:
    # OpenRouter's daily limit resets on the UTC day.
    return now.astimezone(ZoneInfo("UTC")).date().isoformat()
