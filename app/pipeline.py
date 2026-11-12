import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from app import db
from app.ai import AIClient, backup_from_env
from app.checks import check_event, dedupe_key, same_event
from app.models import CandidateEvent, Signals
from app.pages import PageResult, fetch_page
from app.scoring import Reputable, brand_confirmed, domain_of, load_reputable, normalize, reliable_domains, score
from app.sentiment import Opinion, gather_opinions, rate_sentiment
from app.sites import Sites, load as load_sites
from app.sources.nyc_parks import SOURCE as NYC_PARKS, fetch_parks_events
from app.sources.ticketmaster import fetch_events
from app.sources.web import TavilyClient, collect_web_events

log = logging.getLogger(__name__)

MAX_COVERAGE_SEARCHES = 8
MAX_SENTIMENT_EVENTS = 6
# All pasted posts count as one source, so they can't meet the two-source rule alone.
PASTED_POSTS_SOURCE = "pasted posts"


@dataclass
class PipelineDeps:
    http: httpx.Client
    tavily: TavilyClient
    ai: AIClient
    sites: Sites
    reputable: Reputable
    ticketmaster_key: str


@dataclass
class PipelineSummary:
    collected: int
    passed: int
    dropped: int
    eligible: int


@dataclass
class Group:
    """One real event, with every copy of it found across sources."""

    members: list[CandidateEvent]
    captions: list[tuple[str, str]]


def run_pipeline(conn, deps: PipelineDeps, now: datetime) -> PipelineSummary:
    candidates = _collect(deps, now)
    pastes = db.list_pastes(conn)
    groups = _group(candidates, pastes)
    saved = db.saved_event_keys(conn)
    new_paste_keys = {dedupe_key(group.members[0]) for group in groups if group.captions}

    passed = dropped = eligible = 0
    scored_now: list[tuple[Group, CandidateEvent, Signals, list[PageResult]]] = []
    for group in groups:
        key = dedupe_key(_best(group.members, deps.sites))
        if key in saved and key not in new_paste_keys:
            # Scored on an earlier run. Reusing it saves free AI and search requests.
            passed += 1
            eligible += saved[key]
            continue
        try:
            result = _check_group(conn, group, deps, now)
        except Exception:
            log.exception("Could not process an event")
            result = None
        if result is None:
            dropped += 1
            continue
        passed += 1
        scored_now.append(result)

    _add_coverage_from_search(scored_now, deps)
    _add_sentiment(scored_now, deps)
    for _, event, signals, _ in scored_now:
        outcome = score(signals)
        db.save_event(conn, event, outcome)
        eligible += outcome.eligible
    return PipelineSummary(len(candidates) + len(pastes), passed, dropped, eligible)


def _collect(deps: PipelineDeps, now: datetime) -> list[CandidateEvent]:
    candidates: list[CandidateEvent] = []
    sources = (
        ("ticketmaster", lambda: fetch_events(deps.http, deps.ticketmaster_key, now)),
        ("web", lambda: collect_web_events(deps.tavily, deps.http, deps.sites, deps.ai, now)),
        ("nyc_parks", lambda: fetch_parks_events(deps.http, now, lambda e: deps.reputable.named_in(e) is not None)),
    )
    for name, collect in sources:
        try:
            candidates.extend(collect())
        except Exception:
            log.exception("Source failed: %s", name)
    return candidates


def _group(candidates: list[CandidateEvent], pastes: list[tuple]) -> list[Group]:
    groups: list[Group] = []

    def group_for(event: CandidateEvent) -> Group:
        for group in groups:
            if any(same_event(event, member) for member in group.members):
                return group
        group = Group([], [])
        groups.append(group)
        return group

    for event in candidates:
        group_for(event).members.append(event)
    for link, caption, event in pastes:
        group = group_for(event)
        group.members.append(event)
        group.captions.append((link, caption))
    return groups


def _best(members: list[CandidateEvent], sites: Sites) -> CandidateEvent:
    # Prefer an official record, then a reliable page, then anything else.
    rank = lambda e: (e.source_type != "api", not sites.is_reliable(e.url))
    return sorted(members, key=rank)[0]


def _check_group(conn, group: Group, deps: PipelineDeps, now: datetime):
    good: list[CandidateEvent] = []
    pages: list[PageResult] = []
    reasons: list[str] = []
    seen: dict[str, PageResult] = {}
    for member in group.members:
        page = seen.setdefault(member.url, fetch_page(deps.http, member.url, deps.sites))
        result = check_event(member, page, now)
        if result.passed:
            good.append(member)
            pages.append(page)
        else:
            reasons.append(result.reason)
    if not good:
        db.log_dropped(conn, group.members[0], reasons[0])
        return None
    event = _best(good, deps.sites)
    signals = Signals(
        coverage_domains=reliable_domains([m.url for m in good if m.source_type != "api"], deps.sites),
        pasted_post_count=len({link for link, _ in group.captions}),
        official_feed=any(m.source == NYC_PARKS for m in good),
    )
    entry = deps.reputable.match(event)
    if entry:
        reliable_pages = [p for m, p in zip(good, pages) if deps.sites.is_reliable(m.url)]
        signals.reputable_match = True
        signals.brand_confirmed = brand_confirmed(event, entry, reliable_pages)
    return group, event, signals, pages


def _add_coverage_from_search(scored_now: list, deps: PipelineDeps) -> None:
    uncredited = [item for item in scored_now if not score(item[2]).eligible]
    uncredited.sort(key=lambda item: item[1].start)
    for _, event, signals, _ in uncredited[:MAX_COVERAGE_SEARCHES]:
        try:
            hits = deps.tavily.search(f'"{event.name}" {event.venue}', deps.sites.allowed_domains())
        except Exception:
            log.exception("Coverage search failed")
            return
        wanted = normalize(event.name)
        urls = [hit.url for hit in hits if wanted in normalize(hit.content) and domain_of(hit.url) != domain_of(event.url)]
        signals.coverage_domains |= reliable_domains(urls, deps.sites)


def _add_sentiment(scored_now: list, deps: PipelineDeps) -> None:
    shortlist = [item for item in scored_now if score(item[2]).eligible]
    shortlist.sort(key=lambda item: score(item[2]).total, reverse=True)
    for group, event, signals, _ in shortlist[:MAX_SENTIMENT_EVENTS]:
        try:
            opinions = gather_opinions(event, deps.tavily, deps.sites, deps.reputable.match(event))
        except Exception:
            log.exception("Opinion search failed")
            opinions = []
        opinions += [Opinion(PASTED_POSTS_SOURCE, caption) for _, caption in group.captions]
        signals.sentiment = rate_sentiment(opinions, deps.ai)


def _default_deps(conn) -> PipelineDeps:
    http = httpx.Client()
    return PipelineDeps(
        http=http,
        tavily=TavilyClient(http, os.environ["TAVILY_API_KEY"]),
        ai=AIClient(
            http, os.environ["OPENROUTER_API_KEY"], os.environ["OPENROUTER_MODEL"], conn, backup=backup_from_env()
        ),
        sites=load_sites(),
        reputable=load_reputable(),
        ticketmaster_key=os.environ["TICKETMASTER_API_KEY"],
    )


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    load_dotenv()
    conn = db.connect(os.environ.get("DATABASE_PATH", "whatisupnyc.db"))
    summary = run_pipeline(conn, _default_deps(conn), datetime.now(timezone.utc))
    print(
        f"Collected {summary.collected}, passed {summary.passed}, "
        f"dropped {summary.dropped}, eligible {summary.eligible}"
    )
    for reason, count in sorted(db.dropped_reasons(conn).items(), key=lambda item: -item[1]):
        print(f"  Dropped in total, {reason}: {count}")


if __name__ == "__main__":
    main()
