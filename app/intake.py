"""Checks events another tool (Muse) sends to POST /api/intake before they are stored.

Muse is a trusted feed, like Ticketmaster: it holds a key only the builder gave it, and it sends
the end date and picture itself, so the collector saves its events without fetching their pages.
These plain checks are what stands between Muse and readers. Social media links are stored, never
fetched, and never shown.
"""
from datetime import datetime, timedelta
from urllib.parse import urlparse

from app.models import CandidateEvent
from app.pages import clean_image_url

MAX_PER_DAY = 200
MAX_BODY_BYTES = 512_000
DAYS_AHEAD = 60
# Markets and seasonal shows can run all winter; longer than this is more likely a mistake.
LONGEST_RUN_DAYS = 180
SOCIAL_HOSTS = ("instagram.com", "tiktok.com", "xiaohongshu.com", "xhslink.com", "facebook.com", "threads.net")
# Where social apps keep their pictures. Those links expire, and readers must not see a social origin.
SOCIAL_IMAGE_HOSTS = SOCIAL_HOSTS + ("cdninstagram.com", "fbcdn.net", "tiktokcdn.com", "tiktokcdn-us.com", "xhscdn.com")


def _on(host: str, hosts: tuple[str, ...]) -> bool:
    return any(host == h or host.endswith("." + h) for h in hosts)


def _time(text: str, label: str) -> tuple[datetime | None, str]:
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None, f"{label} must be an ISO 8601 date and time"
    if value.tzinfo is None:
        return None, f"{label} needs a time zone offset, like -05:00"
    return value, ""


def check(item, now: datetime) -> tuple[tuple[str, CandidateEvent, str] | None, str]:
    """(post_url, event, caption) when the item is usable, else None and the reason."""
    if not isinstance(item, dict):
        return None, "each event must be an object"
    text = {key: str(item.get(key) or "").strip()
            for key in ("name", "venue", "post_url", "start", "end", "official_url", "image_url")}
    if not text["name"] or len(text["name"]) > 200:
        return None, "name is missing or longer than 200 characters"
    if not text["venue"] or len(text["venue"]) > 200:
        return None, "venue is missing or longer than 200 characters"
    if not text["post_url"].startswith("https://") or len(text["post_url"]) > 500:
        return None, "post_url must be an https link"
    start, reason = _time(text["start"], "start")
    if start is None:
        return None, reason
    end = None
    if text["end"]:
        end, reason = _time(text["end"], "end")
        if end is None:
            return None, reason
        if end <= start:
            return None, "end must be after start"
        if end > start + timedelta(days=LONGEST_RUN_DAYS):
            return None, f"end is more than {LONGEST_RUN_DAYS} days after start"
    if (end or start) <= now:
        return None, "the event has ended" if end else "start is in the past (send end for events that run several days)"
    if start > now + timedelta(days=DAYS_AHEAD):
        return None, f"start is more than {DAYS_AHEAD} days away"
    official = text["official_url"]
    # Readers click this link, and nothing else confirms the event, so every event needs one.
    if not official.startswith("https://") or len(official) > 1000:
        return None, "official_url must be an https link to the event's own page"
    if _on(urlparse(official).hostname or "", SOCIAL_HOSTS):
        return None, "official_url must be the event's own page, not a social media post"
    image = None
    if text["image_url"]:
        image = clean_image_url(text["image_url"]) if len(text["image_url"]) <= 1000 else None
        if image is None:
            return None, "image_url must be an https link to a picture"
        if _on(urlparse(image).hostname or "", SOCIAL_IMAGE_HOSTS):
            return None, "image_url must come from the event's own site, not a social media app"
    event = CandidateEvent(
        name=text["name"],
        start=start,
        end=end,
        venue=text["venue"],
        city="New York",
        url=official,
        source="intake",
        source_type="api",
        category=(str(item.get("category") or "").strip()[:100] or None),
        image_url=image,
    )
    return (text["post_url"], event, str(item.get("caption") or "")[:2000]), ""
