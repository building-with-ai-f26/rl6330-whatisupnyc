# AGENTS.md — Coding Agent Operating Manual

> Give this file to your coding agent and check that it is using these instructions. Keep the stack, commands, and ground rules current.

## 1. Project Overview
- **What this repo builds:** whatisupnyc, a personalized NYC events newsletter. It collects events automatically, checks and scores them, and has AI choose picks for each person only from checked events.
- **Primary user & core workflow:** People new to NYC, starting with NYU classmates and friends. They say what they like and pick their days, then get a short list of events. See the living PRD in `docs/index.html`.
- **Tech stack:** Python 3.12 / uv / FastAPI / Supabase Postgres (through `psycopg`; two projects, dev and production, connection string in `DATABASE_URL`) / Supabase Auth for reader accounts (called only from the server with `httpx`; `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`) / Resend for sign-up and password emails / OpenRouter free model / Tavily / Ticketmaster Discovery API / PostHog analytics (`POSTHOG_KEY`; empty turns it off). Live at whatisupnyc.com on a VPS since 2026-11-09 (see Deploy below); the design doc's Vercel plan came first.
- **AI model:** `nvidia/nemotron-3.5-super-vl:free` through OpenRouter, set by `OPENROUTER_MODEL`. Chosen on 2026-11-02: it returned valid JSON and ignored an injected instruction, while `google/gemma-4-26b-a4b-it:free` returned HTTP 429 and `thinkingmachines/inkling:free` returned HTTP 403. It is slow, about 40 seconds for one extraction. **Backup:** `MiniMax-M3` through MiniMax's own API (`MINIMAX_API_KEY`, `MINIMAX_MODEL`), tried once when OpenRouter fails. It is paid, answers in about a second, and writes `<think>` reasoning before its JSON, which the app removes.
- **Optional source keys:** `NPS_API_KEY` (National Park Service events) and `NYC_EVENTS_CALENDAR_API_KEY` (the city's NYC Events Calendar). An empty key skips that source.
- **Project structure:**
  - `app/main.py`: the web app: the start page, saving readers, the newsletter page, and pasted posts.
  - `app/pipeline.py`: the morning collection run: checks waiting pasted posts, collects from every source, groups copies of the same event, checks, scores, and saves.
  - `app/sources/`: one file per event source (Ticketmaster, NYC Parks, NYU Engage, NPS, NYC Events Calendar, WordPress event calendars, web pages, pasted posts).
  - `app/checks.py`, `app/scoring.py`, `app/sentiment.py`: the plain-code checks, the points, and the review-tone rating.
  - `app/newsletter.py`: picks the 150 events the AI sees, asks the AI to choose, confirms each pick, and builds the list.
  - `app/ai.py`: OpenRouter with the MiniMax backup and the 50-a-day limit. `app/db.py`: every database query. `supabase/migrations/`: the tables, as SQL files applied in name order, with Row Level Security on every table. `app/supabase_auth.py`: sign-up, sign-in, email links and sign-out through Supabase Auth. `app/limits.py`: per-visitor rate limits. `app/auth.py`: refuses forms posted from other sites. Readers' rows are read through `db.as_reader`, which switches to the `authenticated` role so Row Level Security limits each reader to their own rows.
  - `app/templates/`: the pages (`index.html` is the start page, `view.html` the newsletter page, `newsletter.html` the list itself).
  - `data/sites.json`: each site's terms finding and whether we may collect from it. `data/reputable.json`: venues worth reputation points.
  - `tests/`: `uv run pytest`, with saved API responses in `tests/fixtures/`. `scripts/`: key check, the sentiment eval, and `move_readers_to_accounts.py` (one-time: gives readers from before accounts a confirmed account; the only user of `SUPABASE_SECRET_KEY`).
  - `docs/index.html`: the Product Page (living PRD and build log), published with GitHub Pages.

## 2. Commands
- **Install dependencies:** `uv sync`
- **Local Postgres for tests (one time):** `brew install postgresql@17 && brew services start postgresql@17 && /opt/homebrew/opt/postgresql@17/bin/createdb whatisupnyc_test`. Tests use `TEST_DATABASE_URL` (default `postgresql://localhost/whatisupnyc_test`) and never touch Supabase.
- **Apply database changes:** add a new file in `supabase/migrations/`, then `supabase db push --db-url "$DATABASE_URL"` (Supabase CLI: `brew install supabase/tap/supabase`). Dev first, production when deploying.
- **Apply sign-in settings and email templates:** edit `supabase/config.toml` or `supabase/templates/`, preview with `SITE_URL=http://127.0.0.1:8000 supabase config diff --remote=<project ref>`, then `config push` the same way (`RESEND_API_KEY` must be in the shell). `SITE_URL` is `http://127.0.0.1:8000` for dev and `https://whatisupnyc.com` for production. Needs `supabase login`, run once in the Terminal app.
- **Collect events:** `uv run python -m app.pipeline` (takes about 10 minutes on the free model)
- **Run locally:** `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`, then open http://127.0.0.1:8000 and make an account (the dev project's emails arrive through Resend). The login cookies are `Secure`; Chrome and Firefox keep them on `127.0.0.1`.
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

## 5. Deploy (whatisupnyc.com)
- **Where:** a VPS reached with the SSH host alias `ryandri` (in the builder's `~/.ssh/config`). Caddy serves whatisupnyc.com and passes requests to the app on `127.0.0.1:8310`. The server also runs unrelated projects, so touch only whatisupnyc's files and services.
- **Pieces:** code in `/opt/whatisupnyc/app` (a git clone of `main`), Python environment `/opt/whatisupnyc/venv`, settings in `/etc/whatisupnyc/env` (root-only; the server's own keys, separate from the local `.env`; never `SUPABASE_SECRET_KEY`), database: the production Supabase project (`DATABASE_URL` in the server env; the old SQLite file `/var/lib/whatisupnyc/whatisupnyc.db` stays as a backup from before the move), web app `whatisupnyc.service`, daily collector `whatisupnyc-pipeline.timer` (06:30 New York time).
- **Update after merging a PR:**
  1. Back up: on the server, `B=/root/backups/whatisupnyc-$(date +%F) && mkdir -p $B && cp -a /opt/whatisupnyc/app /etc/whatisupnyc/env $B/`. On the Mac, dump the production database (Supabase's free plan has no automatic backups): `pg_dump "$PROD_DATABASE_URL" > ~/backups/whatisupnyc-$(date +%F).sql` (Homebrew's `/opt/homebrew/opt/postgresql@17/bin/pg_dump`).
  2. `cd /opt/whatisupnyc/app && git pull --ff-only`
  2b. Only if the PR added a file in `supabase/migrations/`: from the Mac, `supabase db push --db-url "$PROD_DATABASE_URL"`.
  3. Only if `pyproject.toml` or `uv.lock` changed: `UV_CACHE_DIR=/opt/whatisupnyc/.uv-cache UV_PROJECT_ENVIRONMENT=/opt/whatisupnyc/venv /opt/whatisupnyc/bin/uv sync --frozen --no-dev`
  4. If the PR added an environment variable, add it to `/etc/whatisupnyc/env` as well as `.env.example`.
  5. `systemctl restart whatisupnyc && systemctl is-active whatisupnyc`
  6. Check: the start page loads, sign-in works, and PostHog's Activity page shows a `$pageview` from host `whatisupnyc.com`.
- **Undo:** copy the backup's `app` folder back over `/opt/whatisupnyc/app` and restart. To undo data changes, restore the `pg_dump` file with `psql "$PROD_DATABASE_URL" -f FILE` into an emptied database.
- **Scoring changes apply only to newly collected events.** The collector skips events it already scored, so deploy scoring changes before the 06:30 run.
