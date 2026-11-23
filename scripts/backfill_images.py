"""One-time: give events saved before pictures existed a picture.

Run: uv run python -m scripts.backfill_images   (DATABASE_URL picks dev or production)

Feed pictures come from Ticketmaster, the venue calendars and the National Park Service. Other
upcoming recommended events take the link-preview image of their own page, fetched the same way
the collector does (allowed sites only, robots rules respected). Only empty pictures are filled.
"""
import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from app import db
from app.checks import dedupe_key
from app.models import CandidateEvent
from app.pages import fetch_page, page_image
from app.sites import Sites, load as load_sites


def backfill_images(
    conn: db.Database, http: httpx.Client, sites: Sites, now: datetime, feed_events: list[CandidateEvent]
) -> dict[str, int]:
    counts = {"from_feeds": 0, "pages_checked": 0, "from_pages": 0}
    before = len(_missing(conn, now))
    for event in feed_events:
        if event.image_url:
            db.fill_event_image(conn, dedupe_key(event), event.image_url)
    missing = _missing(conn, now)
    counts["from_feeds"] = before - len(missing)
    for scored in missing:
        if not sites.is_allowed(scored.event.url):
            continue
        counts["pages_checked"] += 1
        page = fetch_page(http, scored.event.url, sites)
        if page.blocked or not page.html:
            continue
        image = page_image(page.html, scored.event.url)
        if image:
            db.fill_event_image_by_id(conn, scored.id, image)
            counts["from_pages"] += 1
    return counts


def _missing(conn: db.Database, now: datetime):
    return [e for e in db.eligible_events(conn, now) if not e.event.image_url]


if __name__ == "__main__":
    from app.sources.nps import fetch_nps_events
    from app.sources.ticketmaster import fetch_events
    from app.sources.wp_events import fetch_wp_events

    load_dotenv()
    now = datetime.now(timezone.utc)
    pool = db.connect(db.url_from_env())
    sites = load_sites()
    with httpx.Client() as http:
        feeds = fetch_events(http, os.environ["TICKETMASTER_API_KEY"], now) + fetch_wp_events(http, sites, now)
        if os.environ.get("NPS_API_KEY"):
            feeds += fetch_nps_events(http, os.environ["NPS_API_KEY"], now)
        print(f"feed events with pictures: {sum(1 for e in feeds if e.image_url)} of {len(feeds)}")
        print(f"upcoming recommended events without a picture: {len(_missing(pool, now))}")
        for name, count in backfill_images(pool, http, sites, now, feeds).items():
            print(f"{name}: {count}")
        print(f"still without a picture: {len(_missing(pool, now))}")
    pool.close()
