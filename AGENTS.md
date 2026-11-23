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
  - `app/newsletter.py`: picks the 150 events the AI sees, asks the AI to choose, confirms each pick, and builds the list. `app/saved_lists.py`: decides whether a reader's saved list from today can be shown or a new one must be made (at most once a New York day, after they save preferences, or with "More like these"), and builds the reader's taste (saved and rated events) for the AI. `app/emailer.py`: emails a reader their own list through Resend. `app/intake.py`: checks events another tool (Muse) sends to `POST /api/intake` (bearer key, stored only as `INTAKE_TOKEN_SHA256`); the morning collection then confirms each on its official page and never fetches the social post.
  - `app/ai.py`: OpenRouter with the MiniMax backup and the 50-a-day limit. `app/db.py`: every database query. `supabase/migrations/`: the tables, as SQL files applied in name order, with Row Level Security on every table. `app/supabase_auth.py`: sign-up, sign-in, email links and sign-out through Supabase Auth. `app/limits.py`: per-visitor rate limits. `app/auth.py`: refuses forms posted from other sites. Readers' rows are read through `db.as_reader`, which switches to the `authenticated` role so Row Level Security limits each reader to their own rows.
  - `app/templates/`: the pages (`index.html` is the start page, `view.html` the newsletter page, `newsletter.html` the list itself, `dashboard.html` the reader's list page with pictures, Save for later, Share, More like these and ratings, `newsletter.html` the compact email version, `event.html` the public page a shared link opens, `preferences.html` editing preferences, `partials/making.html` the "Making your list" screen).
  - `data/sites.json`: each site's terms finding and whether we may collect from it. `data/reputable.json`: venues worth reputation points.
  - `tests/`: `uv run pytest`, with saved API responses in `tests/fixtures/`. `scripts/`: key check and the sentiment eval. (The one-time moves into Supabase and into accounts ran on 2026-11-22/23 and were removed; git history keeps them.)
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
7. **Secrets stay unprinted, even "masked":** check a connection string by parsing it (`urlparse`) and printing only host, port and user; check a key by its prefix and length. Read key lists as JSON and keep only the field you need. *From a real mistake (2026-11-21/22): a `sed` mask stopped at the first `@` inside a database password and printed the rest, and a key listing printed Supabase's legacy `service_role` key in full; the password was reset and the legacy keys disabled.*
8. **Watch the test fail before the fix:** run each new test and see it go red before writing the code that makes it pass, and say so only when it happened. *From a real mistake (2026-11-23): tests were written and the fix applied in one go, then reported as red-to-green; they were checked afterwards against the old code in a separate worktree.*

## 4. Project Rules
- **Sources:** `data/sites.json` records each site's terms finding and the builder's decision. Secret NYC, doNYC, and Eventbrite are prohibited, because their terms ban automated collection. The other listed sites are allowed for this class project; review them before any commercial use. Respect robots rules and blocks. Never fetch TikTok, Instagram, or RedNote links, and don't add login bots, proxies, or block workarounds.
- **No invented events:** plain code checks every event, and code confirms every AI pick came from the checked list.
- **Readers never see** scores, sentiment ratings, or that an event was discovered on a social app.
- **Design doc:** `docs/superpowers/specs/2026-11-02-nyc-events-newsletter-design.md`

## 5. Shipping (deploy to whatisupnyc.com)
The agent ships a change in this order, and stops at anything on the "Ask me first" list below: branch from `main` → small commits with tests (`uv run pytest` green) → dev check (migrations and settings on the dev project first) → push and open a PR → merge → the deploy steps below → verify the live site → the build log line.
- **Where:** a VPS reached with the SSH host alias `ryandri` (in the builder's `~/.ssh/config`). Caddy serves whatisupnyc.com and passes requests to the app on `127.0.0.1:8310`. The server also runs unrelated projects, so touch only whatisupnyc's files and services.
- **Pieces:** code in `/opt/whatisupnyc/app` (a git clone of `main`), Python environment `/opt/whatisupnyc/venv`, settings in `/etc/whatisupnyc/env` (root-only; the server's own keys, separate from the local `.env`; never `SUPABASE_SECRET_KEY`), database: the production Supabase project (`DATABASE_URL` in the server env; the old SQLite file `/var/lib/whatisupnyc/whatisupnyc.db` stays as a backup from before the move), web app `whatisupnyc.service`, daily collector `whatisupnyc-pipeline.timer` (06:30 New York time).
- **Update after merging a PR:**
  1. Back up: on the server, `B=/root/backups/whatisupnyc-$(date +%F) && mkdir -p $B && cp -a /opt/whatisupnyc/app /etc/whatisupnyc/env $B/`. On the Mac, dump the production database (Supabase's free plan has no automatic backups): `pg_dump "$PROD_DATABASE_URL" > ~/backups/whatisupnyc-$(date +%F).sql` (Homebrew's `/opt/homebrew/opt/postgresql@17/bin/pg_dump`).
  2. `cd /opt/whatisupnyc/app && git pull --ff-only`
  2b. Only if the PR added a file in `supabase/migrations/`: from the Mac, `supabase db push --db-url "$PROD_DATABASE_URL"`. If the migration renames or drops anything the running code reads, first `systemctl stop whatisupnyc whatisupnyc-pipeline.timer` (the old code breaks the moment it runs), and start both again in step 5.
  2c. Only if the PR changed `supabase/config.toml` or `supabase/templates/`: `SITE_URL=https://whatisupnyc.com supabase config diff --remote=<production ref>`, then the same with `config push`.
  3. Only if `pyproject.toml` or `uv.lock` changed: `UV_CACHE_DIR=/opt/whatisupnyc/.uv-cache UV_PROJECT_ENVIRONMENT=/opt/whatisupnyc/venv /opt/whatisupnyc/bin/uv sync --frozen --no-dev`
  4. If the PR added an environment variable, add it to `/etc/whatisupnyc/env` as well as `.env.example`.
  5. `systemctl restart whatisupnyc && systemctl is-active whatisupnyc`
  6. Check: the start page loads, sign-in works, and PostHog's Activity page shows a `$pageview` from host `whatisupnyc.com`.
- **One time, for the reader-accounts PR (the move off the site password), in this order:**
  1. Resend shows whatisupnyc.com as Verified. Then `SITE_URL=https://whatisupnyc.com supabase config diff --remote=<production ref>` and `config push`, with `RESEND_API_KEY` in the shell. Without this push, production emails use Supabase's default links, which skip our confirm page, where the owner sets their password.
  2. Back up (step 1 above), and count readers: `select count(*), count(distinct lower(trim(email))) from subscribers`.
  3. `systemctl stop whatisupnyc whatisupnyc-pipeline.timer`. The migration renames `subscribers`, which the old code reads.
  4. From the Mac: `supabase db push --db-url "$PROD_DATABASE_URL"`.
  5. On the server: `git pull --ff-only` and `uv sync` (step 3 above).
  6. In `/etc/whatisupnyc/env`: add `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` (the production project's; without them every page returns 500), and remove `SITE_PASSWORD`.
  7. From the Mac, with the production values set only for this one command: `DATABASE_URL="$PROD_DATABASE_URL" SUPABASE_URL=<production URL> SUPABASE_SECRET_KEY=<production secret key> uv run python -m scripts.move_readers_to_accounts`. Its `readers` count must match the distinct emails from step 2.
  8. `systemctl start whatisupnyc whatisupnyc-pipeline.timer`. Then check: sign up, confirm from the inbox, sign in, sign out, and reset a password.
- **Undo:** copy the backup's `app` folder back over `/opt/whatisupnyc/app` and restart. To undo data changes, restore the `pg_dump` file with `psql "$PROD_DATABASE_URL" -f FILE` into an emptied database.
- **Scoring changes apply only to newly collected events.** The collector skips events it already scored, so deploy scoring changes before the 06:30 run.

## 6. Ask me first
Stop and get the builder's OK, for each one, before:
- **Deleting data or changing a database:** migrations, `truncate`/`delete`, the one-time move scripts, restoring a backup. Production most of all; dev only for data the agent didn't create in this session.
- **Keys, secrets and permissions:** creating, rotating or disabling keys; editing `.env` or `/etc/whatisupnyc/env`; Supabase Auth settings (`supabase config push`); SSH keys and `authorized_keys`; grants and Row Level Security policies.
- **Anything that costs money:** paid AI models or plans, upgrades, new paid services.
- **Production and anything outward-facing:** deploys, stopping or restarting services, merging, pushing, sending email to real readers.

