import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import CandidateEvent, CheckResult
from app.pages import PageResult
from app.sources.ticketmaster import NYC_CITIES

NY = ZoneInfo("America/New_York")
STOP_WORDS = {"vs", "v", "versus", "the", "at", "a", "an", "and", "of", "in", "with", "live", "presents"}


def date_appears(text: str, start: datetime) -> bool:
    local = start.astimezone(NY)
    month_name = local.strftime("%B")
    months = rf"(?:{month_name}|{month_name[:3]})\.?"
    day = rf"0?{local.day}(?:st|nd|rd|th)?"
    patterns = [
        rf"\b{months}\s+{day}\b(?!\d)",
        rf"\b{day}\s+{months}(?![a-z])",
        rf"(?<![\d/]){local.month:02d}/{local.day:02d}\b",
        rf"(?<![\d/]){local.month}/{local.day}\b",
        rf"\b{local.year}-{local.month:02d}-{local.day:02d}\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if _year_matches(text[match.end() :], local.year):
                return True
    return False


def _year_matches(following: str, year: int) -> bool:
    match = re.match(r"\s*[,/]?\s*(\d{4}|\d{2})(?!\d)", following)
    if not match:
        return True
    written = int(match.group(1))
    # Only a slash-style two-digit year or a full year counts; "Nov 7, 8pm" has no year.
    if len(match.group(1)) == 2 and not following.lstrip().startswith("/"):
        return True
    return written in (year, year % 100)


def check_event(event: CandidateEvent, page: PageResult, now: datetime) -> CheckResult:
    if event.start.astimezone(NY) <= now.astimezone(NY):
        return CheckResult(False, "event has passed")
    if event.city.lower() not in NYC_CITIES:
        return CheckResult(False, "not in NYC")
    if event.source_type == "api":
        if page.status is not None and 400 <= page.status < 500 and not page.blocked:
            return CheckResult(False, "page did not open")
        return CheckResult(True)
    if page.blocked:
        return CheckResult(False, "page could not be read")
    if page.status != 200:
        return CheckResult(False, "page did not open")
    if not date_appears(page.text, event.start):
        return CheckResult(False, "date not confirmed on page")
    if _normalize(event.venue) not in _normalize(page.text):
        return CheckResult(False, "venue not confirmed on page")
    return CheckResult(True)


def _normalize(text: str) -> str:
    text = re.sub(r"[’']", "", text.lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _name_words(text: str) -> set[str]:
    return {word for word in _normalize(text).split() if word not in STOP_WORDS}


def dedupe_key(event: CandidateEvent) -> str:
    day = event.start.astimezone(NY).date().isoformat()
    return f"{day}|{' '.join(sorted(_name_words(event.venue)))}|{' '.join(sorted(_name_words(event.name)))}"


def same_event(a: CandidateEvent, b: CandidateEvent) -> bool:
    if a.start.astimezone(NY).date() != b.start.astimezone(NY).date():
        return False
    if _name_words(a.venue) != _name_words(b.venue):
        return False
    words_a, words_b = _name_words(a.name), _name_words(b.name)
    if not words_a or not words_b:
        return False
    return len(words_a & words_b) / min(len(words_a), len(words_b)) >= 0.5
