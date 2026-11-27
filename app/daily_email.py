"""The daily email. Run every hour by whatisupnyc-email.timer: each reader due now gets today's list by email,
made first if they don't have one yet (one AI call, the same one opening the page would make).

Run by hand with: uv run python -m app.daily_email"""
import logging
import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from app import db, saved_lists
from app.ai import AIUnavailable, DailyLimitReached, client_from_env
from app.emailer import send_list
from app.newsletter import NY, render_newsletter

log = logging.getLogger(__name__)

SEND_HOURS = (7, 8, 9, 12, 17)
# AI requests the email job leaves for readers opening their list on the website each day.
AI_RESERVE = 15
SITE_URL = "https://whatisupnyc.com"


def send_due_lists(conn: db.Database, ai, http: httpx.Client, resend_key: str, site_url: str,
                   now: datetime) -> tuple[int, int]:
    """Emails every reader due now. Returns (sent, failed); a failed reader is tried again next hour."""
    sent = failed = 0
    limit = getattr(ai, "daily_limit", 50)
    for reader, email in db.readers_due_an_email(conn, now):
        if not db.claim_daily_email(conn, reader.list_id, now):
            continue  # another run has it
        try:
            outcome = _send_one(conn, ai, http, resend_key, site_url, now, reader, email, limit)
        except DailyLimitReached:
            db.release_daily_email(conn, reader.list_id)
            failed += 1
            break  # no AI left today: the others would fail the same way
        except Exception:
            log.exception("Could not email a reader their list")
            outcome = False
        if outcome is None:
            continue  # nothing new to send today; counts as done
        if outcome:
            sent += 1
        else:
            db.release_daily_email(conn, reader.list_id)
            failed += 1
    return sent, failed


def _send_one(conn, ai, http, resend_key, site_url, now, reader, email, limit) -> bool | None:
    """True when sent, False to try again next hour, None when there is nothing to send today."""
    latest = db.latest_issue(conn, reader.id, reader.list_id)
    if saved_lists.needs_new_list(latest, reader, now):
        if db.count_ai_requests_today(conn, now) >= limit - AI_RESERVE:
            return False  # leave the rest of today's AI for readers on the website
        try:
            saved_lists.make_list(conn, ai, reader, now)
        except DailyLimitReached:
            raise
        except AIUnavailable:
            return False
        latest = db.latest_issue(conn, reader.id, reader.list_id)
    # Only what's still on: a list made this morning can hold a 10 AM walk that's over by a 5 PM email.
    picks = [pick for pick in saved_lists.saved_picks(conn, latest) if not pick.event.event.is_over(now)]
    if not any(not pick.extra for pick in picks):
        return None
    html = render_newsletter(picks, now=now, site_url=site_url)
    local = now.astimezone(NY)
    name = "whatisupnyc" if reader.title in ("", db.FIRST_LIST_TITLE) else reader.title
    subject = f"Your {name} list for {local.strftime('%A, %B %-d')}"
    once = f"daily-{reader.id}-{local.date().isoformat()}" if name == "whatisupnyc" else \
        f"daily-{reader.id}-{reader.list_id}-{local.date().isoformat()}"
    return send_list(http, resend_key, email, subject, html, once_key=once)


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
