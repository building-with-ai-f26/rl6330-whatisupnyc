import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import CandidateEvent, Score, ScoredEvent, Subscriber

NY = ZoneInfo("America/New_York")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
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
    days TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sent_events (
    subscriber_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    PRIMARY KEY (subscriber_id, event_id)
);
CREATE TABLE IF NOT EXISTS ai_requests (
    day TEXT NOT NULL
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def save_event(conn: sqlite3.Connection, event: CandidateEvent, score: Score) -> int:
    cursor = conn.execute(
        "INSERT INTO events (name, start, venue, city, url, source, source_type, organizer,"
        " category, score, eligible, score_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
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
    return cursor.lastrowid


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
        "INSERT INTO subscribers (name, email, interests, look_prompt, days) VALUES (?, ?, ?, ?, ?)",
        (name, email, interests, look_prompt, json.dumps(days)),
    )
    conn.commit()
    return cursor.lastrowid


def get_subscriber(conn: sqlite3.Connection, subscriber_id: int) -> Subscriber | None:
    row = conn.execute("SELECT * FROM subscribers WHERE id = ?", (subscriber_id,)).fetchone()
    if row is None:
        return None
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
