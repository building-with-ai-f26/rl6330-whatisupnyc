"""One-time copy of the old SQLite database into Supabase Postgres.

Run: uv run python -m scripts.move_to_supabase PATH_TO_OLD.db
The target is DATABASE_URL. It must already have the tables (supabase db push) and no rows.
"""
import json
import sqlite3
import sys
from datetime import date, datetime

from dotenv import load_dotenv

from app import db

# Old SQLite text or integers that become real Postgres types, by table and column.
CONVERT = {
    ("events", "start"): datetime.fromisoformat,
    ("events", "eligible"): bool,
    ("pastes", "start"): datetime.fromisoformat,
    ("subscribers", "days"): json.loads,
    ("ai_requests", "day"): date.fromisoformat,
}
HAS_ID = ("events", "dropped_events", "subscribers", "pastes", "waiting_pastes")


def move(sqlite_path: str, target: db.Database) -> dict[str, int]:
    """Copy every table, keeping IDs. Returns the number of rows copied per table."""
    old = sqlite3.connect(sqlite_path)
    old.row_factory = sqlite3.Row
    counts = {}
    with target.connection() as c:
        for table in db.TABLES:
            if c.execute(f"SELECT EXISTS (SELECT 1 FROM {table}) AS has_rows").fetchone()["has_rows"]:
                raise RuntimeError(f"Target database is not empty: {table}")
        # One transaction, so a failure part way copies nothing.
        with c.transaction():
            for table in db.TABLES:
                rows = [_convert(table, dict(row)) for row in old.execute(f"SELECT * FROM {table}")]
                if rows:
                    columns = list(rows[0])
                    c.cursor().executemany(
                        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
                        [[row[column] for column in columns] for row in rows],
                    )
                counts[table] = len(rows)
            # New rows must get IDs after the copied ones.
            for table in HAS_ID:
                c.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), coalesce(max(id), 0) + 1, false)"
                    f" FROM {table}"
                )
    old.close()
    return counts


def _convert(table: str, row: dict) -> dict:
    for (convert_table, column), convert in CONVERT.items():
        if convert_table == table and row.get(column) is not None:
            row[column] = convert(row[column])
    if table == "subscribers" and not row.get("token"):
        row["token"] = db._new_token()
    return row


if __name__ == "__main__":
    load_dotenv()
    if len(sys.argv) != 2:
        sys.exit("Usage: uv run python -m scripts.move_to_supabase PATH_TO_OLD.db")
    pool = db.connect(db.url_from_env())
    for table, count in move(sys.argv[1], pool).items():
        print(f"{table}: {count}")
    pool.close()
