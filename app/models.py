from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

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

    def __post_init__(self):
        if self.start.tzinfo is None:
            raise ValueError("Event start must include a time zone")


@dataclass
class CheckResult:
    passed: bool
    reason: str | None = None


@dataclass
class Signals:
    reputable_match: bool = False
    # Listed by the city agency that runs the event, through an official feed.
    official_feed: bool = False
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
    id: int
    name: str
    email: str
    interests: str
    look_prompt: str
    days: list[str]


@dataclass
class Pick:
    event: ScoredEvent
    reason: str
