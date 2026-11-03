# whatisupnyc Build 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local prototype that automatically collects NYC events, checks and scores them, and produces a personalized newsletter on screen for about 5 real people, which the builder then emails by hand.

**Architecture:** One small Python app. A pipeline command collects candidates, runs plain-code checks, and scores what passes, saving results to SQLite. A FastAPI page saves each person's preferences and asks a free AI model to choose only from the checked events. Code then confirms every pick and renders the newsletter.

**Tech Stack:** Python 3.12+, uv, FastAPI, Jinja2, SQLite (`sqlite3`), httpx, BeautifulSoup, pytest, OpenRouter free model, Tavily, Ticketmaster Discovery API.

**Spec:** `docs/superpowers/specs/2026-11-02-nyc-events-newsletter-design.md`

## Global Constraints

- Everything is free to run. Do not add a paid service or a paid API plan.
- Allowed sources only. Never fetch TikTok, Instagram, or RedNote URLs. No login bots, proxies, headless browsers, block workarounds, or scraping tools using a platform's internal API.
- Plain code does all checks. The AI may only choose from events that passed, and code confirms every pick.
- Never send a subscriber's name or email to the AI model.
- Readers never see scores, sentiment ratings, an "unknown" label, or that an event was discovered on TikTok, Instagram, RedNote, or Luma. A Luma page may be the link when the event has no better page.
- Secrets stay in `.env`. Add every new variable to `.env.example` with a placeholder. Never print key values.
- The server binds to `127.0.0.1` only. Instagram and automatic email sending are out of scope for Build 1.
- Work on branch `build-1-prototype`, never directly on `main`. Do not push unless the builder asks. End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Tests never call real services. Inject fake HTTP and AI clients.
- If a fix fails twice in a row, stop, summarize, and ask the builder (`AGENTS.md` rule 6).

## Review Focus

1. **The free AI model is rate limited, removed, or returns invalid JSON:** the app does not crash, keeps collected events, and shows a plain message. Tests in Tasks 4 and 7.
2. **Nothing or few events match someone's preferences:** the issue is short or says so, never padded. Test in Task 6.
3. **A date is written differently on the page, or an event is late tonight:** `Sat, Nov 7` matches `2026-11-07`, and New York time decides whether an event has passed. Test in Task 3.
4. **A messy paste:** a RedNote caption in Chinese, or a caption with no date, gives a real event or a clear "couldn't find the date" message, never an invented date. Test in Task 7.
5. **The same event from two sources under different names, or a repeat issue:** it appears once, and a person is not sent an event already sent to them. Tests in Tasks 3 and 6.

---

## Before Task 1: Builder actions

The builder gets free keys for Ticketmaster (developer.ticketmaster.com) and Tavily (tavily.com) and adds `TICKETMASTER_API_KEY` and `TAVILY_API_KEY` to `.env`. Do not start Task 1 without them.

## File map

| File | Responsibility |
|---|---|
| `app/models.py` | Shared dataclasses |
| `app/db.py` | SQLite tables and reads and writes |
| `app/sites.py` | Reads `data/sites.json`, the record of allowed and reliable sites |
| `app/pages.py` | Polite page fetching, robots check, page text |
| `app/ai.py` | OpenRouter client and daily request limit |
| `app/sources/ticketmaster.py` | Ticketmaster candidates |
| `app/sources/web.py` | Tavily search, schema.org reading, AI extraction |
| `app/sources/pasted.py` | Turns a pasted post into a checked-page candidate |
| `app/checks.py` | The plain-code event checks and de-duplication |
| `app/scoring.py` | Credibility points and eligibility |
| `app/sentiment.py` | Gathers outside opinions and rates them |
| `app/newsletter.py` | Choosing, confirming, and rendering |
| `app/pipeline.py` | The `collect → check → score` command |
| `app/main.py`, `app/templates/` | Web pages |
| `data/sites.json`, `data/reputable.json` | Site decisions and the reputable list |

---

### Task 1: Project setup, verified sources, and data model

**Files:**
- Create: `pyproject.toml`, `app/__init__.py`, `app/models.py`, `app/db.py`, `app/sites.py`, `data/sites.json`, `data/reputable.json`, `scripts/check_keys.py`, `tests/test_db.py`, `tests/test_sites.py`
- Modify: `.env.example`, `.gitignore` (add `*.db`)

**Interfaces:**
- Produces:
  - `CandidateEvent(name: str, start: datetime, venue: str, city: str, url: str, source: str, source_type: Literal["api","web","pasted"], organizer: str | None = None, category: str | None = None)`, where `start` must be timezone-aware
  - `CheckResult(passed: bool, reason: str | None)`
  - `Signals(reputable_match: bool, brand_confirmed: bool, past_coverage_count: int, coverage_domains: set[str], pasted_post_count: int, sentiment: Literal["positive","mixed","negative","unknown"])`
  - `Score(total: int, eligible: bool, reason: str | None)`
  - `ScoredEvent(id: int, event: CandidateEvent, score: Score)`
  - `Subscriber(id: int, name: str, email: str, interests: str, look_prompt: str, days: list[str])`
  - `Pick(event: ScoredEvent, reason: str)`
  - `db.connect(path: str) -> sqlite3.Connection`, `db.save_event(conn, event: CandidateEvent, score: Score) -> int`, `db.eligible_events(conn, now: datetime) -> list[ScoredEvent]`, `db.log_dropped(conn, event, reason: str)`, `db.save_subscriber(conn, ...) -> int`, `db.get_subscriber(conn, id) -> Subscriber`, `db.mark_sent(conn, subscriber_id: int, event_ids: list[int])`, `db.sent_event_ids(conn, subscriber_id: int) -> set[int]`, `db.count_ai_requests_today(conn) -> int`, `db.record_ai_request(conn)`
  - `sites.load(path="data/sites.json") -> Sites`, `Sites.is_allowed(url: str) -> bool`, `Sites.is_reliable(url: str) -> bool`, `Sites.allowed_domains() -> list[str]`

- [ ] **Step 1: Create the branch and set up the project**

Run: `git checkout -b build-1-prototype`. Create `pyproject.toml` with `requires-python = ">=3.12"`, dependencies `fastapi`, `uvicorn`, `jinja2`, `python-multipart`, `httpx`, `beautifulsoup4`, `python-dotenv`, and dev dependency `pytest`. Run `uv sync`.
Expected: `uv run pytest --version` prints a version.

- [ ] **Step 2: Test the keys without printing them**

`scripts/check_keys.py` makes one small call each to OpenRouter (`GET https://openrouter.ai/api/v1/key`), Ticketmaster, and Tavily, printing only `OK` or `FAILED` and the HTTP status for each. Add `OPENROUTER_MODEL`, `TICKETMASTER_API_KEY`, and `TAVILY_API_KEY` to `.env.example`.
Run: `uv run python scripts/check_keys.py`
Expected: three `OK` lines. If one fails, stop and tell the builder.

- [ ] **Step 3: Verify limits and terms on each provider's own site**

Read the official pages for OpenRouter free limits, Tavily free credits, and Ticketmaster's terms. Read the terms and `robots.txt` for Luma, Secret NYC, Time Out, doNYC, The Skint, NYC Parks, and Eventbrite. Record each in `data/sites.json` as `{"domain": ..., "automated_access": "allowed"|"prohibited", "reliable": bool, "terms_url": ..., "checked_on": "YYYY-MM-DD"}`. Terms that say nothing count as allowed. Set `reliable` to true for editorial, official venue, organizer, and city sites, and false for Luma and Eventbrite.

**Stop rule:** if fewer than two web sites remain allowed, stop and report to the builder, because the product would skew toward big ticketed shows.

Report any provider limit that differs from the spec to the builder before continuing.

- [ ] **Step 4: Draft the reputable list**

Write about 30 well-known NYC organizers, venues, and series in `data/reputable.json` as `{"name": ..., "aliases": [...], "official_domain": ...}`, and show the list to the builder for edits.

- [ ] **Step 5: Write failing tests**

```python
def test_prohibited_site_not_allowed(sites): assert not sites.is_allowed("https://blocked.example/e/1")
def test_unknown_site_not_allowed(sites): assert not sites.is_allowed("https://never-reviewed.example/")
def test_luma_allowed_but_not_reliable(sites):
    assert sites.is_allowed("https://lu.ma/x") and not sites.is_reliable("https://lu.ma/x")
def test_naive_datetime_rejected():
    with pytest.raises(ValueError): CandidateEvent(name="A", start=datetime(2026,11,7), venue="V", city="Brooklyn", url="https://x.test", source="t", source_type="api")
def test_eligible_events_excludes_ineligible_and_past(conn): ...  # save one each; expect only the future eligible event
def test_sent_ids_round_trip(conn): db.mark_sent(conn, 1, [2,3]); assert db.sent_event_ids(conn, 1) == {2,3}
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py tests/test_sites.py -v`
Expected: FAIL with import errors.

- [ ] **Step 7: Implement `app/models.py`, `app/db.py`, and `app/sites.py`**

Enforce timezone-aware `start` in `CandidateEvent.__post_init__`. Tables: `events`, `dropped_events`, `subscribers`, `sent_events`, `ai_requests`. A domain missing from `sites.json` is not allowed.

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock app data scripts tests .env.example .gitignore docs/superpowers
git commit -m "feat: add project setup, verified sites, and data model"
```

---

### Task 2: Ticketmaster source

**Files:**
- Create: `app/sources/__init__.py`, `app/sources/ticketmaster.py`, `tests/fixtures/ticketmaster.json`, `tests/test_ticketmaster.py`

**Interfaces:**
- Consumes: `CandidateEvent`.
- Produces: `fetch_events(client: httpx.Client, api_key: str, now: datetime, size: int = 100) -> list[CandidateEvent]`

- [ ] **Step 1: Write failing tests using a small fixture and `httpx.MockTransport`**

```python
def test_maps_fields():
    e = fetch_events(client, "k", NOW)[0]
    assert (e.name, e.venue, e.city, e.source_type) == ("Show", "Beacon Theatre", "New York", "api")
    assert e.start.tzinfo is not None and e.url.startswith("https://")
def test_skips_record_without_venue_or_datetime(): assert names(fetch_events(client,"k",NOW)) == ["Show"]
def test_keeps_only_nyc_cities(): assert "Newark" not in cities(fetch_events(client,"k",NOW))
def test_error_status_returns_empty_list(): assert fetch_events(error_client,"k",NOW) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ticketmaster.py -v`
Expected: FAIL with an import error.

- [ ] **Step 3: Implement `fetch_events` against `https://app.ticketmaster.com/discovery/v2/events.json`**

Use `stateCode=NY`, `startDateTime` from `now`, and `size`. Read venue and city from `_embedded.venues[0]`, the organizer from `promoter.name`, and the category from `classifications[0].segment.name`. NYC cities are New York, Manhattan, Brooklyn, Queens, Bronx, and Staten Island, plus common Queens neighborhoods such as Flushing and Long Island City. Log and skip malformed records.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ticketmaster.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/sources tests
git commit -m "feat: collect events from Ticketmaster"
```

---

### Task 3: Event checks

**Files:**
- Create: `app/pages.py`, `app/checks.py`, `tests/test_checks.py`, `tests/test_pages.py`

**Interfaces:**
- Consumes: `CandidateEvent`, `CheckResult`, `Sites`.
- Produces:
  - `PageResult(status: int | None, text: str, html: str, blocked: bool)`
  - `fetch_page(client: httpx.Client, url: str, sites: Sites) -> PageResult` sets `blocked=True` without a request when the site isn't allowed or `robots.txt` disallows the URL. It sends the user agent `whatisupnyc-bot/0.1 (+https://whatisupnyc.com)`.
  - `date_appears(text: str, start: datetime) -> bool`
  - `check_event(event: CandidateEvent, page: PageResult, now: datetime) -> CheckResult`
  - `dedupe_key(event: CandidateEvent) -> str`
  - `same_event(a: CandidateEvent, b: CandidateEvent) -> bool`

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.parametrize("text", ["Sat, Nov 7", "November 7, 2026", "11/7/2026", "2026-11-07"])
def test_date_formats_match(text): assert date_appears(text, dt(2026,11,7,20))
def test_wrong_date_fails(): assert not check_event(web_event(), page("Nov 9 at Beacon Theatre"), NOW).passed
def test_wrong_venue_fails(): assert not check_event(web_event(), page("Nov 7 at Other Hall"), NOW).passed
def test_dead_link_fails(): assert check_event(web_event(), PageResult(404,"","",False), NOW).reason == "page did not open"
def test_past_uses_new_york_time(): assert not check_event(event_at_ny(hour=20), good_page, ny(21)).passed
def test_late_tonight_still_upcoming(): assert check_event(event_at_ny(hour=23), good_page, ny(21)).passed
def test_outside_nyc_fails(): assert not check_event(web_event(city="Newark"), good_page, NOW).passed
def test_blocked_web_page_fails(): assert not check_event(web_event(), PageResult(None,"","",True), NOW).passed
def test_api_event_passes_when_blocked(): assert check_event(api_event(), PageResult(None,"","",True), NOW).passed
def test_api_event_fails_on_404(): assert not check_event(api_event(), PageResult(404,"","",False), NOW).passed
def test_same_event_across_names():
    assert same_event(ev("New York Knicks vs. Boston Celtics"), ev("Knicks v Celtics"))
def test_different_nights_not_same(): assert not same_event(ev("Show", day=7), ev("Show", day=8))
def test_fetch_page_never_requests_disallowed_site(): ...  # assert transport received no request
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_checks.py tests/test_pages.py -v`
Expected: FAIL with import errors.

- [ ] **Step 3: Implement `app/pages.py` and `app/checks.py`**

Page text comes from BeautifulSoup, with scripts and styles removed. Compare the current time in `America/New_York`. Venue matching ignores case and punctuation. `same_event` requires the same New York date and venue, plus at least half of the significant name words in common after removing words such as "vs", "v", "the", and "at".

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_checks.py tests/test_pages.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app tests
git commit -m "feat: add event checks and duplicate detection"
```

---

### Task 4: AI client and web source

**Files:**
- Create: `app/ai.py`, `app/sources/web.py`, `tests/test_ai.py`, `tests/test_web.py`

**Interfaces:**
- Consumes: `db.count_ai_requests_today`, `db.record_ai_request`, `Sites`, `fetch_page`, `CandidateEvent`.
- Produces:
  - `class AIUnavailable(Exception)`
  - `AIClient(http: httpx.Client, api_key: str, model: str, conn, daily_limit: int = 50)`
  - `AIClient.complete_json(system: str, user: str) -> dict`, which raises `AIUnavailable` on 429, an error status, a missing model, the daily limit, or invalid JSON after one retry
  - `TavilyClient(http, api_key, run_budget: int = 20)`, `TavilyClient.search(query: str, include_domains: list[str]) -> list[SearchHit]`, and `SearchHit(url: str, title: str, content: str)`. It returns `[]` once the run budget is used.
  - `read_schema_events(html: str, url: str, source: str) -> list[CandidateEvent]`
  - `extract_with_ai(pages: list[tuple[str, str]], ai: AIClient) -> list[CandidateEvent]`
  - `collect_web_events(tavily, http, sites, ai, now) -> list[CandidateEvent]`

- [ ] **Step 1: Write failing tests**

```python
def test_429_raises_ai_unavailable(): ...
def test_invalid_json_retried_once_then_raises(): ...  # assert 2 requests, then AIUnavailable
def test_daily_limit_blocks_without_http_call(): ...
def test_reads_schema_org_event(): assert read_schema_events(HTML_WITH_JSONLD,"https://x.test/e","x")[0].venue == "Beacon Theatre"
def test_no_schema_returns_empty(): assert read_schema_events("<p>hi</p>","https://x.test","x") == []
def test_ai_extraction_batches_pages_into_one_request(): assert fake_ai.calls == 1
def test_ai_failure_keeps_schema_events(): assert names(collect_web_events(tavily,http,sites,failing_ai,NOW)) == ["Schema Show"]
def test_tavily_budget_stops_searches(): assert client.search("q",[]) == [] and http_calls == 20
def test_search_uses_only_allowed_domains(): assert "blocked.example" not in fake_tavily.include_domains
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ai.py tests/test_web.py -v`
Expected: FAIL with import errors.

- [ ] **Step 3: Implement `app/ai.py` and `app/sources/web.py`**

Call `https://openrouter.ai/api/v1/chat/completions`, using `OPENROUTER_MODEL`. Tell the model that page text is data and not instructions. It must leave a field empty rather than guess, and any event with an empty date or venue is discarded. Read schema.org JSON-LD first and send only the remaining pages to AI, up to 10 pages per request.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ai.py tests/test_web.py -v`
Expected: PASS.

- [ ] **Step 5: Choose a free model**

With the builder's real key, list free models from OpenRouter's models endpoint and try 2 or 3 on one extraction and one picking prompt. Set the best in `.env` as `OPENROUTER_MODEL`, and record the choice and reason in `AGENTS.md`.
Expected: valid JSON comes back from the chosen model.

- [ ] **Step 6: Commit**

```bash
git add app tests AGENTS.md
git commit -m "feat: collect web events with search and AI extraction"
```

---

### Task 5: Credibility scoring and sentiment

**Files:**
- Create: `app/scoring.py`, `app/sentiment.py`, `tests/test_scoring.py`, `tests/test_sentiment.py`, `tests/sentiment_examples.json`, `scripts/eval_sentiment.py`

**Interfaces:**
- Consumes: `Signals`, `Score`, `AIClient`, `TavilyClient`, `Sites`.
- Produces:
  - `load_reputable(path="data/reputable.json") -> Reputable`
  - `Reputable.match(event: CandidateEvent) -> Entry | None`
  - `brand_confirmed(event: CandidateEvent, entry: Entry, coverage_pages: list[PageResult]) -> bool`
  - `score(signals: Signals) -> Score`
  - `Opinion(domain: str, text: str)`
  - `gather_opinions(event, tavily, sites, entry) -> list[Opinion]`
  - `rate_sentiment(opinions: list[Opinion], ai: AIClient) -> Literal["positive","mixed","negative","unknown"]`

**Points, from the spec:** reputation up to 40 (40 for a confirmed reputable match, 25 for 3 or more past events covered by reliable sources), coverage up to 30 (10 per separate reliable domain), interest up to 20 (5 per pasted post), sentiment up to 10 (positive 10, mixed 5, unknown 0).

- [ ] **Step 1: Write failing tests**

```python
def test_reputable_event_scores_40(): assert score(sig(reputable_match=True, brand_confirmed=True)).total == 40
def test_no_credibility_is_excluded(): assert not score(sig()).eligible
def test_unconfirmed_brand_is_excluded(): assert not score(sig(reputable_match=True, brand_confirmed=False)).eligible
def test_coverage_only_is_eligible(): assert score(sig(coverage_domains={"timeout.com"})).total == 10
def test_coverage_caps_at_30(): assert score(sig(coverage_domains=FOUR_DOMAINS)).total == 30
def test_interest_alone_is_excluded(): assert not score(sig(pasted_post_count=4)).eligible
def test_negative_is_excluded(): assert not score(sig(reputable_match=True, brand_confirmed=True, sentiment="negative")).eligible
def test_unknown_still_eligible_with_no_points(): assert score(sig(coverage_domains={"timeout.com"}, sentiment="unknown")).total == 10
def test_luma_only_unknown_host_excluded(): ...  # Luma domain in coverage is ignored, so not eligible
def test_opinions_exclude_own_page_and_organizer_domain(): ...
def test_fewer_than_two_domains_is_unknown_without_ai_call(): assert rate_sentiment([op("a.com")], ai) == "unknown" and ai.calls == 0
def test_rating_with_fake_quote_is_unknown(): assert rate_sentiment(two_ops, ai_returning_fake_quote) == "unknown"
def test_rating_with_real_quote_is_kept(): assert rate_sentiment(two_ops, ai_positive_real_quote) == "positive"
def test_ai_unavailable_is_unknown(): assert rate_sentiment(two_ops, failing_ai) == "unknown"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_scoring.py tests/test_sentiment.py -v`
Expected: FAIL with import errors.

- [ ] **Step 3: Implement `app/scoring.py` and `app/sentiment.py`**

`coverage_domains` holds only domains where `Sites.is_reliable` is true, enforced where signals are built. An API or official-domain event confirms its brand directly. Otherwise, a reliable page must mention the brand and the event. Run sentiment on a shortlist of eligible events only, and reuse saved scores rather than re-scoring.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scoring.py tests/test_sentiment.py -v`
Expected: PASS.

- [ ] **Step 5: Add the sentiment evaluation**

`tests/sentiment_examples.json` holds about 20 real text examples, gathered with their source URLs, with an empty `label` field. `scripts/eval_sentiment.py` prints accuracy and each disagreement.
Run after the builder fills in the labels: `uv run python scripts/eval_sentiment.py`
Expected: an accuracy figure for the build log. The builder does the labeling, so do not fill in labels for them.

- [ ] **Step 6: Commit**

```bash
git add app tests scripts
git commit -m "feat: add credibility scoring and sentiment rating"
```

---

### Task 6: Choosing, confirming, and rendering a newsletter

**Files:**
- Create: `app/newsletter.py`, `app/templates/newsletter.html`, `tests/test_newsletter.py`

**Interfaces:**
- Consumes: `ScoredEvent`, `Subscriber`, `Pick`, `AIClient`, `db.sent_event_ids`.
- Produces:
  - `choose_picks(subscriber: Subscriber, events: list[ScoredEvent], ai: AIClient, max_picks: int = 5) -> list[Pick]`
  - `confirm_picks(raw: list[dict], events: list[ScoredEvent]) -> list[Pick]`
  - `render_newsletter(picks: list[Pick]) -> str`

- [ ] **Step 1: Write failing tests**

```python
def test_invented_id_removed(): assert confirm_picks([{"id":999,"reason":"x"}], EVENTS) == []
def test_duplicate_id_kept_once(): assert len(confirm_picks([{"id":1,"reason":"a"},{"id":1,"reason":"b"}], EVENTS)) == 1
def test_reason_naming_a_discovery_source_replaced():
    assert "tiktok" not in confirm_picks([{"id":1,"reason":"Viral on TikTok"}], EVENTS)[0].reason.lower()
def test_prompt_has_no_name_or_email(): assert "Ana" not in fake_ai.prompt and "ana@x.test" not in fake_ai.prompt
def test_prompt_lists_higher_scores_first(): ...
def test_already_sent_excluded_by_choose(): assert 1 not in fake_ai.prompt_ids
def test_no_events_makes_no_ai_call(): assert choose_picks(sub, [], fake_ai) == [] and fake_ai.calls == 0
def test_short_issue_says_so(): assert "only" in render_newsletter(ONE_PICK).lower()
def test_empty_issue_says_so(): assert "no new picks" in render_newsletter([]).lower()
def test_reader_never_sees_internals():
    html = render_newsletter(PICKS).lower()
    assert not any(w in html for w in ["score","sentiment","unknown","credibility"])
def test_luma_link_allowed_when_only_page(): assert "lu.ma/x" in render_newsletter(LUMA_PICK)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_newsletter.py -v`
Expected: FAIL with an import error.

- [ ] **Step 3: Implement `app/newsletter.py` and the template**

`choose_picks` sends only `interests`, `look_prompt`, and each event's id, name, date, venue, and category, ordered by score. It excludes already-sent events. The look prompt can change tone, categories, and the number of picks up to `max_picks`, but not the layout. A reason naming TikTok, Instagram, RedNote, Xiaohongshu, or Luma is replaced with a neutral line. Show "only" wording when fewer than 3 picks exist. `AIUnavailable` propagates to the caller.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_newsletter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app tests
git commit -m "feat: choose, confirm, and render newsletters"
```

---

### Task 7: Pipeline, web pages, and pasted social finds

**Files:**
- Create: `app/sources/pasted.py`, `app/pipeline.py`, `app/main.py`, `app/templates/index.html`, `app/templates/view.html`, `tests/test_pasted.py`, `tests/test_pipeline.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `process_paste(link: str, caption: str, tavily, http, sites, ai, now) -> PasteResult`, where `PasteResult(event: CandidateEvent | None, message: str)`
  - `PipelineDeps(http: httpx.Client, tavily: TavilyClient, ai: AIClient, sites: Sites, reputable: Reputable, ticketmaster_key: str)`
  - `run_pipeline(conn, deps: PipelineDeps, now: datetime) -> PipelineSummary(collected: int, passed: int, dropped: int, eligible: int)`, runnable as `uv run python -m app.pipeline`
  - FastAPI routes: `GET /`, `POST /subscribers`, `GET /newsletter/{id}`, `POST /newsletter/{id}/sent`, `POST /paste`

**Templates:** "New to NYC", "Free and cheap", "Live music and comedy", and "Art and culture". Each one fills the "what kind of event?" bar with editable text.

- [ ] **Step 1: Write failing tests**

```python
def test_paste_never_fetches_the_social_url(): process_paste(TIKTOK, CAPTION, ...); assert "tiktok.com" not in requested_hosts
def test_paste_uses_reliable_page_url(): assert "tiktok" not in process_paste(TIKTOK, CAPTION, ...).event.url
def test_paste_with_no_date_gives_clear_message():
    r = process_paste(RED, "很棒的展览", ...)
    assert r.event is None and "date" in r.message
def test_chinese_caption_with_details_works(): assert process_paste(RED, "11月7日 Beacon Theatre 音乐会", ...).event
def test_paste_with_no_reliable_page_dropped(): assert process_paste(LINK, CAPTION, tavily_empty, ...).event is None
def test_pipeline_logs_dropped_with_reason(): ...
def test_pipeline_survives_one_source_failing(): assert run_pipeline(conn, deps_web_fails, NOW).passed > 0
def test_pipeline_merges_same_event_across_sources(): ...  # one row, and the reliable source counts as coverage
def test_blank_interests_rejected(): assert client.post("/subscribers", data=NO_INTERESTS).status_code == 400
def test_start_page_has_bar_templates_days(): ...  # "what kind of event?", all 4 templates, Monday to Sunday
def test_ai_down_shows_plain_message(): assert "try again" in client.get("/newsletter/1").text.lower()
def test_mark_sent_excludes_next_time(): ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pasted.py tests/test_pipeline.py tests/test_main.py -v`
Expected: FAIL with import errors.

- [ ] **Step 3: Implement `app/sources/pasted.py`**

Validate the link as a URL, but never fetch it. AI reads only the caption and leaves unknown fields empty. Search allowed domains for the event, then run `check_event` on the page found. Each paste for the same event raises `pasted_post_count` by one.

- [ ] **Step 4: Implement `app/pipeline.py`**

Order: Ticketmaster and web collection, de-duplication, checks, signals, score, and save. One source failing is logged and the others continue. Merging a duplicate keeps the best link and adds reliable domains to coverage. Print the `PipelineSummary` plus the top drop reasons.

- [ ] **Step 5: Implement `app/main.py` and the pages**

The start page shows the "what kind of event?" bar, the four templates, the day picker, an optional look prompt, name and email fields, and a paste box. The newsletter page shows the rendered issue with a copy button and a "mark as sent" button. Catch `AIUnavailable` and show: "The AI model is busy right now. Your events are saved, so please try again in a little while." Include a note in the newsletter telling readers to reply by email with feedback.

- [ ] **Step 6: Run all tests**

Run: `uv run pytest -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app tests
git commit -m "feat: add pipeline, web pages, and pasted social finds"
```

---

### Task 8: Real run, measured checks, and class documents

**Files:**
- Modify: `AGENTS.md`, `README.md`, `docs/index.html`
- Create: `docs/assets/prototype.png`

**Interfaces:**
- Consumes: the running app.
- Produces: the Build 1 evidence the builder needs.

- [ ] **Step 1: Run the real pipeline**

Run: `uv run python -m app.pipeline`
Expected: a summary with at least 10 eligible events from at least 2 sources. If not, or if eligible events are mostly from one source, stop and report the numbers to the builder.

- [ ] **Step 2: Measure the checks**

Open about 20 passed events by hand-sampling their pages and compare date, venue, and link. Record the error count, sample size, and the AI and search requests used, for the builder's build log.
Expected: real numbers. Report them as measured even if they look bad.

- [ ] **Step 3: Try it in the browser**

Run: `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`. Create one subscriber from a template, generate a newsletter, paste a real caption, and confirm the issue shows only checked events with working links.
Expected: all of that works. Save a screenshot at `docs/assets/prototype.png` for the Product Page.

- [ ] **Step 4: Update `AGENTS.md` and `README.md`**

Fill in the project summary, stack, and the commands `uv sync`, `uv run python -m app.pipeline`, `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`, and `uv run pytest`. Replace the README placeholders you know, and leave the builder's pod and handle for the builder.

- [ ] **Step 5: Draft PRD v1 on `docs/index.html`**

Fill in the name, pitch, target user, before and after, core workflow, the three non-goals, riskiest assumption, architecture, and the screenshot. Leave the build log's learning, costs, and hours as placeholders for the builder. Never invent them. Spend the builder has reported may be filled in.

- [ ] **Step 6: Commit and hand back**

```bash
git add AGENTS.md README.md docs
git commit -m "docs: add Build 1 setup, PRD draft, and prototype evidence"
```

Tell the builder what remains theirs: confirm 5 people's preferences and send issues; label the sentiment examples; complete Terminus Episodes 1 and 2; fill in costs, hours, and learning; rename the repo to `rl6330-whatisupnyc`; merge and push on at least two separate days; enable GitHub Pages; submit both URLs in Icerynk; and check Icerynk for anything this plan missed.
