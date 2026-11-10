# AGENTS.md — Coding Agent Operating Manual

> Give this file to your coding agent and check that it is using these instructions. Keep the stack, commands, and ground rules current.

## 1. Project Overview
- **What this repo builds:** whatisupnyc, a personalized NYC events newsletter. It collects events automatically, checks and scores them, and has AI choose picks for each person only from checked events.
- **Primary user & core workflow:** People new to NYC, starting with NYU classmates and friends. They say what they like and pick their days, then get a short list of events. See the living PRD in `docs/index.html`.
- **Tech stack:** Python 3.12 / uv / FastAPI / SQLite / OpenRouter free model / Tavily / Ticketmaster Discovery API. Vercel is the planned host from Build 5, which requires the database and login work listed in the design doc.
- **AI model:** `nvidia/nemotron-3.5-super-vl:free` through OpenRouter, set by `OPENROUTER_MODEL`. Chosen on 2026-11-02: it returned valid JSON and ignored an injected instruction, while `google/gemma-4-26b-a4b-it:free` returned HTTP 429 and `thinkingmachines/inkling:free` returned HTTP 403. It is slow, about 40 seconds for one extraction.

## 2. Commands
- **Install dependencies:** `uv sync`
- **Collect events:** `uv run python -m app.pipeline` (takes about 10 minutes on the free model)
- **Run locally:** `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`, then open http://127.0.0.1:8000 and enter `SITE_PASSWORD` from `.env` (any name works). Every page needs that password, and the site stays closed without one. Newsletters open only by their private link.
- **Run deterministic tests:** `uv run pytest`
- **Run model evals:** `uv run python -m scripts.eval_sentiment`, after labeling `tests/sentiment_examples.json`
- **Check API keys:** `uv run python scripts/check_keys.py`

## 3. Non-Negotiable Engineering Rules
1. **Plan before coding:** Before touching multiple files, propose a brief plan and wait for approval.
2. **Small batches:** Make small, focused changes that can be reviewed and explained by a human in a single Pull Request.
3. **Explainability:** Avoid unnecessary abstractions or dependencies. Help the human builder understand the main components, what changed, why it matters, what could break, and how it was checked.
4. **Verify changes:** Run relevant checks. For a reproducible logic bug, use a failing test to demonstrate the fix when practical. Record manual checks and known limitations; follow the selected Build 4 evaluation track.
5. **Zero secrets in code:** Never hardcode API keys, tokens, or credentials. Always read from environment variables (`.env`) and update `.env.example` when new variables are needed.
6. **Stop when looping:** If a fix fails twice in a row, stop, summarize what failed, and ask the human before trying a third approach.

## 4. Project Rules
- **Sources:** `data/sites.json` records each site's terms finding and the builder's decision. Secret NYC, doNYC, and Eventbrite are prohibited, because their terms ban automated collection. The other listed sites are allowed for this class project; review them before any commercial use. Respect robots rules and blocks. Never fetch TikTok, Instagram, or RedNote links, and don't add login bots, proxies, or block workarounds.
- **No invented events:** plain code checks every event, and code confirms every AI pick came from the checked list.
- **Readers never see** scores, sentiment ratings, or that an event was discovered on a social app.
- **Design doc:** `docs/superpowers/specs/2026-11-02-nyc-events-newsletter-design.md`
