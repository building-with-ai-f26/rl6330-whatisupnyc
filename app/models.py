from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, NamedTuple

SourceType = Literal["api", "web", "pasted"]
Sentiment = Literal["positive", "mixed", "negative", "unknown"]


@dataclass
class CandidateEvent:
    name: str
    start: datetime
    venue: str
    city: str
    url: str
    source: str
    source_type: SourceType
    organizer: str | None = None
    category: str | None = None
    # https only; None shows a plain block instead of a picture.
    image_url: str | None = None
    # For events that run several days, like markets and sample sales; None for one-off events.
    end: datetime | None = None

    def __post_init__(self):
        if self.start.tzinfo is None:
            raise ValueError("Event start must include a time zone")
        if self.end is not None and self.end.tzinfo is None:
            raise ValueError("Event end must include a time zone")

    def is_over(self, now: datetime) -> bool:
        """Over once it ends; an event with no end date is over once it starts."""
        return (self.end or self.start) <= now


@dataclass
class CheckResult:
    passed: bool
    reason: str | None = None


@dataclass
class Signals:
    reputable_match: bool = False
    # Feeds that list this event, like "nyc_parks" or "ticketmaster". Web pages are counted as coverage instead.
    feeds: set[str] = field(default_factory=set)
    brand_confirmed: bool = False
    coverage_domains: set[str] = field(default_factory=set)
    pasted_post_count: int = 0
    sentiment: Sentiment = "unknown"


@dataclass
class Score:
    total: int
    eligible: bool
    reason: str | None = None


@dataclass
class ScoredEvent:
    id: int
    event: CandidateEvent
    score: Score


@dataclass
class Subscriber:
    # The reader's account ID (a UUID, as text). Their email stays in Supabase Auth.
    id: str
    name: str
    interests: str
    look_prompt: str
    days: list[str]
    # When the reader last saved preferences; a list made before this is out of date.
    changed_at: datetime | None = None


@dataclass
class Pick:
    event: ScoredEvent
    reason: str
    # Added by "More like these", shown at the top of the list.
    more_like: bool = False
    # Picked in the same AI call but kept behind "Show more" until the reader asks.
    extra: bool = False


class SavedPick(NamedTuple):
    event_id: int
    reason: str
    more_like: bool = False
    extra: bool = False


@dataclass
class SavedIssue:
    """A list the AI made for a reader, as saved, in the AI's order."""

    made_on: date
    made_at: datetime
    picks: list[SavedPick]
