"""The daily email. Run every hour by whatisupnyc-email.timer: each reader due now gets today's list by email,
made first if they don't have one yet (one AI call, the same one opening the page would make).

Run by hand with: uv run python -m app.daily_email"""
import logging
import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from app import db, saved_lists
from app.ai import AIUnavailable, client_from_env
from app.emailer import send_list
from app.newsletter import NY, render_newsletter

log = logging.getLogger(__name__)

SEND_HOURS = (7, 8, 9, 12, 17)
SITE_URL = "https://whatisupnyc.com"


def send_due_lists(conn: db.Database, ai, http: httpx.Client, resend_key: str, site_url: str,
                   now: datetime) -> tuple[int, int]:
    """Emails every reader due now. Returns (sent, failed); a failed reader is tried again next hour."""
    sent = failed = 0
    for reader, email in db.readers_due_an_email(conn, now):
        try:
            latest = db.latest_issue(conn, reader.id)
            if saved_lists.needs_new_list(latest, reader, now):
                saved_lists.make_list(conn, ai, reader, now)
                latest = db.latest_issue(conn, reader.id)
            picks = saved_lists.saved_picks(conn, latest)
            html = render_newsletter(picks, now=latest.made_at, site_url=site_url)
            day = latest.made_at.astimezone(NY).strftime("%A, %B %-d")
            ok = send_list(http, resend_key, email, f"Your whatisupnyc list for {day}", html)
        except AIUnavailable:
            ok = False
        except Exception:
            log.exception("Could not email a reader their list")
            ok = False
        if ok:
            db.mark_emailed(conn, reader.id, now)
            sent += 1
        else:
            failed += 1
    return sent, failed


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    load_dotenv()
    conn = db.connect(db.url_from_env())
    http = httpx.Client()
    sent, failed = send_due_lists(conn, client_from_env(http, conn), http, os.environ["RESEND_API_KEY"],
                                  os.environ.get("SITE_URL", SITE_URL), datetime.now(timezone.utc))
    print(f"Emailed {sent} lists, {failed} to try again next hour")


if __name__ == "__main__":
    main()
