import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import CandidateEvent, CheckResult
from app.pages import PageResult
from app.sources.ticketmaster import NYC_CITIES

NY = ZoneInfo("America/New_York")
NAME_SPAN_CHARS = 200
CHARS_BEFORE_NAME = 200
CHARS_AFTER_NAME = 500
WEEKDAY_PREFIX_CHARS = 40
WEEKDAY_MAX_DAYS_AWAY = 7
WEEKDAYS = [
    r"mon|monday",
    r"tue|tues|tuesday",
    r"wed|weds|wednesday",
    r"thu|thur|thurs|thursday",
    r"fri|friday",
    r"sat|saturday",
    r"sun|sunday",
]
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
    return _confirm_on_page(event, page.text, now)


def _confirm_on_page(event: CandidateEvent, text: str, now: datetime) -> CheckResult:
    """A listing page holds many events, so details must sit near this event's name."""
    cleaned = re.sub(r"[’']", "", text.lower())
    spans = _name_spans(cleaned, event.name)
    if not spans:
        return CheckResult(False, "event name not found on page")
    venue = normalize_text(event.venue)
    date_found = venue_found = False
    for start, end in spans:
        nearby = cleaned[max(0, start - CHARS_BEFORE_NAME) : end + CHARS_AFTER_NAME]
        has_date = date_appears(nearby, event.start) or _weekday_before(cleaned, start, event.start, now)
        has_venue = venue in normalize_text(nearby)
        if has_date and has_venue:
            return CheckResult(True)
        date_found = date_found or has_date
        venue_found = venue_found or has_venue
    if not date_found:
        return CheckResult(False, "date not confirmed on page")
    if not venue_found:
        return CheckResult(False, "venue not confirmed on page")
    return CheckResult(False, "date and venue not confirmed together")


def _name_spans(cleaned: str, name: str) -> list[tuple[int, int]]:
    words = [word for word in normalize_text(name).split() if word not in STOP_WORDS]
    if not words:
        return []
    spans = []
    for match in re.finditer(rf"\b{re.escape(words[0])}\b", cleaned):
        chunk = cleaned[match.start() : match.start() + NAME_SPAN_CHARS]
        ends = [re.search(rf"\b{re.escape(word)}\b", chunk) for word in words]
        if all(ends):
            spans.append((match.start(), match.start() + max(found.end() for found in ends)))
    return spans


def _weekday_before(cleaned: str, position: int, start: datetime, now: datetime) -> bool:
    # Daily listings often write only "tues 11am: event name".
    local = start.astimezone(NY)
    if (local.date() - now.astimezone(NY).date()).days > WEEKDAY_MAX_DAYS_AWAY:
        return False
    prefix = cleaned[max(0, position - WEEKDAY_PREFIX_CHARS) : position]
    return re.search(rf"\b(?:{WEEKDAYS[local.weekday()]})\b", prefix) is not None


def normalize_text(text: str) -> str:
    text = re.sub(r"[’']", "", text.lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def name_words(text: str) -> set[str]:
    return {word for word in normalize_text(text).split() if word not in STOP_WORDS}


def dedupe_key(event: CandidateEvent) -> str:
    day = event.start.astimezone(NY).date().isoformat()
    return f"{day}|{' '.join(sorted(name_words(event.venue)))}|{' '.join(sorted(name_words(event.name)))}"


def same_event(a: CandidateEvent, b: CandidateEvent) -> bool:
    if a.start.astimezone(NY).date() != b.start.astimezone(NY).date():
        return False
    if name_words(a.venue) != name_words(b.venue):
        return False
    words_a, words_b = name_words(a.name), name_words(b.name)
    if not words_a or not words_b:
        return False
    return len(words_a & words_b) / min(len(words_a), len(words_b)) >= 0.5
