"""Checks events another tool sends to POST /api/intake before they are stored.

Every event is still confirmed on its official page by the morning collection, like every other
source; this only refuses what can't be right. Social media links are stored, never fetched.
"""
from datetime import datetime, timedelta
from urllib.parse import urlparse

from app.models import CandidateEvent

MAX_PER_DAY = 200
MAX_BODY_BYTES = 512_000
DAYS_AHEAD = 60
SOCIAL_HOSTS = ("instagram.com", "tiktok.com", "xiaohongshu.com", "xhslink.com", "facebook.com", "threads.net")


def check(item, now: datetime) -> tuple[tuple[str, CandidateEvent, str] | None, str]:
    """(post_url, event, caption) when the item is usable, else None and the reason."""
    if not isinstance(item, dict):
        return None, "each event must be an object"
    text = {key: str(item.get(key) or "").strip() for key in ("name", "venue", "post_url", "start", "official_url")}
    if not text["name"] or len(text["name"]) > 200:
        return None, "name is missing or longer than 200 characters"
    if not text["venue"] or len(text["venue"]) > 200:
        return None, "venue is missing or longer than 200 characters"
    if not text["post_url"].startswith("https://") or len(text["post_url"]) > 500:
        return None, "post_url must be an https link"
    try:
        start = datetime.fromisoformat(text["start"])
    except ValueError:
        return None, "start must be an ISO 8601 date and time"
    if start.tzinfo is None:
        return None, "start needs a time zone offset, like -05:00"
    if start <= now:
        return None, "start is in the past"
    if start > now + timedelta(days=DAYS_AHEAD):
        return None, f"start is more than {DAYS_AHEAD} days away"
    official = text["official_url"]
    if official:
        host = urlparse(official).hostname or ""
        if not official.startswith("https://") or len(official) > 1000:
            return None, "official_url must be an https link"
        if any(host == social or host.endswith("." + social) for social in SOCIAL_HOSTS):
            return None, "official_url must be the event's own page, not a social media post"
    event = CandidateEvent(
        name=text["name"],
        start=start,
        venue=text["venue"],
        city="New York",
        url=official,
        source="intake",
        source_type="web",
        category=(str(item.get("category") or "").strip()[:100] or None),
    )
    return (text["post_url"], event, str(item.get("caption") or "")[:2000]), ""
