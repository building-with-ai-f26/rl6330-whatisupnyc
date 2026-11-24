"""Each reader's list is made by the AI at most once a New York day, then saved and shown again.

A new list is made when the reader has none from today, or saved new preferences after their
latest list. Making one is the slow part (the free model takes about a minute); showing a
saved one is instant.
"""
from datetime import datetime

from app import db
from app.ai import AIClient, AIUnavailable
from app.models import Pick, SavedIssue, Subscriber
from app.newsletter import NY, Taste, choose_picks

# One AI call picks this many; the list shows the first PICKS_SHOWN and keeps the rest behind
# "Show more", already in the page, so showing them needs no wait.
PICKS_MADE = 16
PICKS_SHOWN = 8


def needs_new_list(latest: SavedIssue | None, reader: Subscriber, now: datetime) -> bool:
    if latest is None or latest.made_on != now.astimezone(NY).date():
        return True
    return reader.changed_at is not None and reader.changed_at > latest.made_at


def saved_picks(conn: db.Database, latest: SavedIssue) -> list[Pick]:
    """The saved list's picks, in order, leaving out any event that no longer exists."""
    events = db.events_by_ids(conn, [saved.event_id for saved in latest.picks])
    return [Pick(events[saved.event_id], saved.reason, saved.more_like, saved.extra)
            for saved in latest.picks if saved.event_id in events]


def taste_for(conn: db.Database, reader: Subscriber, more_like_ids: list[int] | None = None) -> Taste:
    """The reader's saved and rated events, and any they asked for more like, by name."""
    saved = db.saved_for_later_ids(conn, reader.id)
    rated = db.ratings(conn, reader.id)
    more = list(more_like_ids or [])
    events = db.events_by_ids(conn, saved + list(rated) + more)

    def names(ids):
        # Only events the site still recommends steer the AI.
        return [events[i].event.name for i in ids if i in events and events[i].score.eligible]

    return Taste(
        saved=names(saved),
        liked=names([i for i, stars in rated.items() if stars >= 4]),
        disliked=names([i for i, stars in rated.items() if stars <= 2]),
        more_like=names(more),
    )


def make_list(
    conn: db.Database, ai: AIClient, reader: Subscriber, now: datetime, more_like_ids: list[int] | None = None
) -> list[Pick]:
    """Asks the AI for a new list and saves it. If the AI is unavailable, nothing is saved.

    With more_like_ids ("More like these"), the new picks go on top of the reader's current list,
    which stays as it was."""
    events, sent = db.eligible_events(conn, now), db.sent_event_ids(conn, reader.id)
    current: list[Pick] = []
    if more_like_ids:
        latest = db.latest_issue(conn, reader.id)
        current = [Pick(p.event, p.reason, False, p.extra) for p in saved_picks(conn, latest)] if latest else []
    # Never offer again what's already on the list, shown or behind "Show more".
    skip = sent | {pick.event.id for pick in current}
    taste = taste_for(conn, reader, more_like_ids)
    chosen = choose_picks(reader, events, ai, now=now, max_picks=PICKS_MADE, sent_ids=skip, taste=taste)
    if not chosen and any(event.id not in skip for event in events):
        # There were events to offer but no usable answer: try again next time, like a busy AI,
        # rather than saving an empty list for the rest of the day.
        raise AIUnavailable("the AI's answer had no usable picks")
    new = [Pick(p.event, p.reason, bool(more_like_ids), n >= PICKS_SHOWN) for n, p in enumerate(chosen)]
    picks = new + current
    db.save_issue(conn, reader.id, picks, now)
    return picks
