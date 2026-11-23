"""Each reader's list is made by the AI at most once a New York day, then saved and shown again.

A new list is made when the reader has none from today, or saved new preferences after their
latest list. Making one is the slow part (the free model takes about a minute); showing a
saved one is instant.
"""
from datetime import datetime

from app import db
from app.ai import AIClient, AIUnavailable
from app.models import Pick, SavedIssue, Subscriber
from app.newsletter import NY, choose_picks


def needs_new_list(latest: SavedIssue | None, reader: Subscriber, now: datetime) -> bool:
    if latest is None or latest.made_on != now.astimezone(NY).date():
        return True
    return reader.changed_at is not None and reader.changed_at > latest.made_at


def saved_picks(conn: db.Database, latest: SavedIssue) -> list[Pick]:
    """The saved list's picks, in order, leaving out any event that no longer exists."""
    events = db.events_by_ids(conn, [event_id for event_id, _ in latest.picks])
    return [Pick(events[event_id], reason) for event_id, reason in latest.picks if event_id in events]


def make_list(conn: db.Database, ai: AIClient, reader: Subscriber, now: datetime) -> list[Pick]:
    """Asks the AI for a new list and saves it. If the AI is unavailable, nothing is saved."""
    events, sent = db.eligible_events(conn, now), db.sent_event_ids(conn, reader.id)
    picks = choose_picks(reader, events, ai, now=now, sent_ids=sent)
    if not picks and any(event.id not in sent for event in events):
        # There were events to offer but no usable answer: try again next time, like a busy AI,
        # rather than saving an empty list for the rest of the day.
        raise AIUnavailable("the AI's answer had no usable picks")
    db.save_issue(conn, reader.id, picks, now)
    return picks
