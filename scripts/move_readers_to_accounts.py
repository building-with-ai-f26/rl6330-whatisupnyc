"""One-time move of readers from before accounts into Supabase Auth accounts with profiles.

Run: uv run python -m scripts.move_readers_to_accounts
Reads DATABASE_URL, SUPABASE_URL and SUPABASE_SECRET_KEY. Only this script uses the secret key.

Each email becomes one account, already confirmed, so the reader can use "Forgot password" to set
a password, and nobody else can sign up with their email. Emails that differ only in case or spaces
are one reader: the newest row's preferences win, and every row's sent events are kept.
Safe to run again: existing accounts, profiles and sent events are left as they are.
"""
import os
import sys

import httpx
from dotenv import load_dotenv

from app import db

PER_PAGE = 1000


def move_readers(conn: db.Database, http: httpx.Client, url: str, secret_key: str) -> dict[str, int]:
    admin = f"{url.rstrip('/')}/auth/v1/admin/users"
    headers = {"apikey": secret_key, "Authorization": f"Bearer {secret_key}"}
    readers = _readers(conn)
    accounts = _existing_accounts(http, admin, headers)
    counts = {"readers": len(readers), "accounts_created": 0, "accounts_found": 0, "profiles": 0, "sent_events": 0}
    for email in readers:
        if email in accounts:
            counts["accounts_found"] += 1
            continue
        response = http.post(admin, headers=headers, json={"email": email, "email_confirm": True}, timeout=30)
        response.raise_for_status()
        accounts[email] = response.json()["id"]
        counts["accounts_created"] += 1
    # The accounts exist now; the profiles and sent events go in together or not at all.
    with conn.connection() as c, c.transaction():
        for email, (newest, subscriber_ids) in readers.items():
            user_id = accounts[email]
            counts["profiles"] += c.execute(
                "INSERT INTO profiles (user_id, name, interests, look_prompt, days) VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (user_id) DO NOTHING",
                (user_id, newest["name"], newest["interests"], newest["look_prompt"], newest["days"]),
            ).rowcount
            event_ids = [
                row["event_id"]
                for row in c.execute(
                    "SELECT DISTINCT event_id FROM old_sent_events WHERE subscriber_id = ANY(%s) ORDER BY event_id",
                    (subscriber_ids,),
                )
            ]
            for event_id in event_ids:
                counts["sent_events"] += c.execute(
                    "INSERT INTO sent_events (user_id, event_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (user_id, event_id),
                ).rowcount
    return counts


def _readers(conn: db.Database) -> dict[str, tuple[dict, list[int]]]:
    """Each reader's email (trimmed, lowercase): their newest row and every row's ID."""
    with conn.connection() as c:
        rows = c.execute("SELECT * FROM old_subscribers ORDER BY id").fetchall()
    readers: dict[str, tuple[dict, list[int]]] = {}
    for row in rows:
        email = row["email"].strip().lower()
        ids = readers[email][1] if email in readers else []
        readers[email] = (row, ids + [row["id"]])  # rows are in ID order, so the last one is the newest
    return readers


def _existing_accounts(http: httpx.Client, admin: str, headers: dict) -> dict[str, str]:
    accounts: dict[str, str] = {}
    page = 1
    while True:
        response = http.get(admin, headers=headers, params={"page": page, "per_page": PER_PAGE}, timeout=30)
        response.raise_for_status()
        users = response.json()["users"]
        accounts.update({user["email"].lower(): user["id"] for user in users if user.get("email")})
        if len(users) < PER_PAGE:
            return accounts
        page += 1


if __name__ == "__main__":
    load_dotenv()
    missing = [name for name in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY") if not os.environ.get(name)]
    if missing:
        sys.exit(f"Set {', '.join(missing)} first.")
    pool = db.connect(db.url_from_env())
    with httpx.Client() as http:
        for name, count in move_readers(pool, http, os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"]).items():
            print(f"{name}: {count}")
    pool.close()
