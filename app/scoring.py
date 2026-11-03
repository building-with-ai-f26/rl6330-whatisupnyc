import json
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.models import CandidateEvent, Score, Signals
from app.pages import PageResult
from app.sites import Sites

REPUTABLE_POINTS = 40
PAST_COVERAGE_POINTS = 25
PAST_COVERAGE_NEEDED = 3
POINTS_PER_COVERAGE_DOMAIN = 10
MAX_COVERAGE_POINTS = 30
POINTS_PER_PASTED_POST = 5
MAX_INTEREST_POINTS = 20
MIN_CONFIRMING_NAME_WORDS = 2
BRAND_NEAR_EVENT_CHARS = 300
SENTIMENT_POINTS = {"positive": 10, "mixed": 5, "unknown": 0, "negative": 0}


def score(signals: Signals) -> Score:
    if signals.reputable_match and signals.brand_confirmed:
        reputation = REPUTABLE_POINTS
    elif signals.past_coverage_count >= PAST_COVERAGE_NEEDED:
        reputation = PAST_COVERAGE_POINTS
    else:
        reputation = 0
    coverage = min(MAX_COVERAGE_POINTS, POINTS_PER_COVERAGE_DOMAIN * len(signals.coverage_domains))
    interest = min(MAX_INTEREST_POINTS, POINTS_PER_PASTED_POST * signals.pasted_post_count)
    total = reputation + coverage + interest + SENTIMENT_POINTS[signals.sentiment]
    if signals.sentiment == "negative":
        return Score(total, False, "negative sentiment")
    if reputation == 0 and coverage == 0:
        return Score(total, False, "no credibility signal")
    return Score(total, True, None)


def reliable_domains(urls: list[str], sites: Sites) -> set[str]:
    return {domain_of(url) for url in urls if sites.is_reliable(url)}


def domain_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def normalize(text: str) -> str:
    text = re.sub(r"[’']", "", text.lower())
    return " " + " ".join(re.sub(r"[^a-z0-9]+", " ", text).split()) + " "


@dataclass
class Entry:
    name: str
    aliases: list[str]
    official_domain: str

    def names(self) -> list[str]:
        return [self.name, *self.aliases]


class Reputable:
    def __init__(self, entries: list[Entry]):
        self.entries = entries

    def match(self, event: CandidateEvent) -> Entry | None:
        fields = normalize(f"{event.organizer or ''} | {event.venue} | {event.name}")
        for entry in self.entries:
            if any(normalize(name) in fields for name in entry.names()):
                return entry
        return None


def load_reputable(path: str = "data/reputable.json") -> Reputable:
    with open(path) as f:
        return Reputable([Entry(e["name"], e["aliases"], e["official_domain"]) for e in json.load(f)])


def brand_confirmed(event: CandidateEvent, entry: Entry, coverage_pages: list[PageResult]) -> bool:
    if event.source_type == "api":
        return True
    domain = domain_of(event.url)
    if domain == entry.official_domain or domain.endswith(f".{entry.official_domain}"):
        return True
    event_name = normalize(event.name)
    if len(event_name.split()) < MIN_CONFIRMING_NAME_WORDS:
        return False
    for page in coverage_pages:
        text = normalize(page.text)
        position = text.find(event_name)
        if position == -1:
            continue
        nearby = text[max(0, position - BRAND_NEAR_EVENT_CHARS) : position + len(event_name) + BRAND_NEAR_EVENT_CHARS]
        if any(normalize(name) in nearby for name in entry.names()):
            return True
    return False
