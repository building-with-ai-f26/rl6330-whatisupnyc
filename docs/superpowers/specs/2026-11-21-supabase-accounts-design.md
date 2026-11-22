# Supabase, reader accounts, and editable preferences

Date: 2026-11-21. Status: approved by Ryan, 2026-11-21.

## Intent

An app improvement (not Build 3 work). Today the whole site sits behind one shared pod password, every reader is a row in SQLite, and a reader can only come back through a secret newsletter link and can never change what they entered. After this change:

- Anyone can open whatisupnyc.com; the pod password is gone.
- A reader makes an account with email and password, the site remembers their preferences, and they can sign in later to see their list and edit those preferences.
- All app data (events and readers) lives in Supabase (hosted Postgres with built-in sign-in), set up securely.

## Goals

1. Move every table from SQLite to Supabase Postgres. Events, scoring and the morning collector keep working exactly as today.
2. Email-and-password accounts through Supabase Auth, with email confirmation and "Forgot password".
3. Start-page flow stays questions first; the last step (which already asks name and email) also asks for a password and creates the account.
4. A signed-in reader can open their newsletter and an "Edit my preferences" page, and can sign out.
5. Existing readers keep their preferences: they claim them through "Forgot password" with the email they used.
6. Secure by default: Row Level Security on every table, secret keys only on the server, sessions in cookies scripts can't read.

## Non-goals

- Emailing the newsletter automatically. Ryan wants it; it is the **next project**, with its own design (the email sender set up here is its groundwork).
- The reader dashboard: save events for later, "more like these", and rating events they went to so the AI picks from that. Ryan wants it; it is the **project after**, with its own design. This project's saved lists are its foundation.
- A builder-only page for viewing every reader's list. Not needed once emailing is automatic.
- Changing email or password from inside the site (beyond "Forgot password"), deleting an account, sign-in with Google or magic links.
- Supabase code running in the browser. The browser only talks to our FastAPI server.
- Changing collection, checks, scoring or how the AI picks.

## Anti-goals (a failure even if it works)

- Any reader can see or change another reader's preferences or list.
- A secret key (`sb_secret_…`, the database password, OpenRouter, MiniMax) reaches the browser or git.
- The site goes public with nothing stopping one visitor from using up the 50-a-day AI limit or flooding sign-ups and pasted posts.
- The collector loses or rescores existing events during the move.

## Constraints

- Supabase's built-in email sender only mails the project's own team, 2 emails an hour. The builder (Ryan) will connect his own sender (custom SMTP, e.g. Resend) on whatisupnyc.com before sign-up goes live. That is his task: it needs his domain and accounts.
- Supabase free plan: no automatic backups; a project pauses after 7 days with no activity (the daily collector keeps production active). Two free projects are allowed.
- The VPS may not reach Supabase's direct database address (IPv6 only on free plan); connect through Supabase's session pooler (IPv4).
- No Docker on the builder's Mac. Local Postgres comes from Homebrew.
- AGENTS.md rules: small reviewable PRs, plan before multi-file changes, secrets only in `.env` / `/etc/whatisupnyc/env`, update `.env.example`.
- Build 3 is due 2026-11-23 at noon. This work may land before or after; it should not leave `main` broken in between.

## Approaches considered

- **A. Supabase for sign-in and readers only, events stay in SQLite.** Least change, but reader data and event data split across two databases.
- **B. Supabase only for sign-in, preferences stay in SQLite.** Least work, but reader information isn't in Supabase and Row Level Security protects nothing.
- **C. Everything in Supabase, events included. Chosen by Ryan.** One database for the whole app. The cost is smaller than first estimated: all database code is in `app/db.py` (279 lines, 7 tables), called only by the collector, the AI limit counter, the web app and tests.
- Ruled out: writing our own password system.

## What was decided

### Database
- Two Supabase projects: **dev** (local work and manual testing) and **production** (the VPS). Builder's call, matching the course's "separate dev and production databases".
- The server connects directly to Postgres with a connection string in `DATABASE_URL` (secret), using `psycopg` (v3). This connection is the privileged owner role, used for events, pastes, AI counts and the collector.
- `app/db.py` keeps its function names and return types so callers barely change; queries move to Postgres syntax. `sqlite3.Row` usage, `INTEGER` booleans and text dates become Postgres types where it's simple (builder's call which).
- Schema lives in git as numbered SQL migration files (`supabase/migrations/`), applied to dev and production with the Supabase CLI (`brew install supabase/tap/supabase`, `supabase db push`). This replaces `CREATE TABLE IF NOT EXISTS` at startup.
- Readers: a `profiles` table keyed by the Supabase Auth user ID (`uuid`, referencing `auth.users`), holding name, interests, extra wishes (`look_prompt`) and days. Email stays in Supabase Auth, not copied. `sent_events` is keyed by that user ID.
- **Saved lists:** a `newsletter_issues` table (reader user ID, date made, event IDs in order). The newsletter page shows the reader's latest saved list. A new list is made (one AI call) only when there is none from today (New York time) or the reader just saved new preferences. Its events are written to `sent_events` when it is made, so the next list doesn't repeat them. This replaces "Copy this list" and "Mark as sent" on the reader's page. Builder's call on columns.
- **Row Level Security on every table.** Event-side tables get RLS with no policies, so Supabase's public API can read or write nothing in them. `profiles`, `sent_events` and `newsletter_issues` get policies that let the `authenticated` role select/update only rows where the user ID equals `auth.uid()`.
- Profile and sent-event queries from the server run inside a transaction that switches to the `authenticated` role with the reader's verified user ID (`set local role authenticated` plus `request.jwt.claims`), so the policies apply even if our code has a bug. Builder's call (defense in depth).

### Sign-in
- Server-side only. FastAPI calls Supabase Auth's HTTP API with `httpx` (already a dependency): sign up, sign in with password, send password reset, get user, refresh session, sign out. No new Supabase library.
- After sign-in, the access and refresh tokens go in cookies marked `HttpOnly`, `Secure`, `SameSite=Lax`. Each request checks the access token with Supabase's "get user" call, refreshing when it has expired. Builder's call: no local token-verification library.
- Keep the existing check that forms come from this site (`auth.sent_from_this_site`), since cookies now carry the login. Remove the pod password check and `SITE_PASSWORD`.
- Keys: `SUPABASE_URL` and the publishable key (`sb_publishable_…`) for Auth calls, on the server; `SUPABASE_SECRET_KEY` (`sb_secret_…`) only for the one-time migration script, never in the web app. All in `.env` / `/etc/whatisupnyc/env`, listed in `.env.example`.

### Reader flow
- **New reader:** the start page's steps as today; the last step asks name, email and **password**. Submitting creates the Supabase account, saves the profile (server inserts it with the new user ID), and shows the first list right away. Builder's call: the "show it right away" uses a random one-time token on the profile (like today's newsletter token), in a cookie, valid for 24 hours (it simply expires; the server doesn't need to learn when the email is confirmed). This happens only when sign-up created a brand-new account and no profile exists for that user ID yet. A banner says "Confirm your email to come back and edit your picks."
- If the email already has an account (Supabase's sign-up reply then has no new identity, or a profile already exists for that ID), the page shows the same "Check your email to confirm, or sign in / reset your password if you already have an account" message and saves nothing. This doesn't reveal whether an address is registered and stops anyone from taking over another reader's profile by signing up with their email.
- **Returning reader:** "Sign in" link at the top of the start page → email + password → their newsletter (`/newsletter`, no token in the URL).
- **Edit preferences:** `/preferences`, a plain form prefilled with interests, extra wishes, days and name. Saving updates the profile and returns to the newsletter.
- **Forgot password:** email box → Supabase sends a reset link → a page on our site sets the new password.
- **Sign out** link on the newsletter and preferences pages.
- Old `/newsletter/{token}` links stop working (show a page pointing to sign-in / forgot password).
- A failed AI call shows today's "AI is busy" message; the reader's previous saved list, if any, still shows.

### Moving existing data
- A one-time script (`scripts/move_to_supabase.py`) copies every SQLite table into Postgres, keeping event IDs so `sent_events` still matches. For each existing reader it creates a Supabase Auth user with their email, **already confirmed** (via the secret key), and a profile, so "Forgot password" works for them and nobody can claim their email by signing up again. If one email appears in several `subscribers` rows, the newest row's preferences win and every row's `sent_events` are merged. Run against dev first, then production after a backup.

### Public site safety
- Per-visitor rate limits (by client IP, read from Caddy's forwarded header only when the request comes from 127.0.0.1) on sign-up, sign-in, password reset, pasted posts and the newsletter page (which calls the AI). Over the limit returns `429`. In-memory limiter is fine for one server process. Builder sets the numbers.
- The 50-a-day AI limit stays.

### Tests
- `uv run pytest` keeps working offline. Tests run against a local Postgres from Homebrew (`brew install postgresql@17`), with a tiny stand-in for Supabase's `auth` schema (`auth.users`, `auth.uid()` reading `request.jwt.claims`) so the RLS policies can be tested. Supabase Auth HTTP calls are faked with saved responses, like the other APIs in `tests/fixtures/`.
- Required tests: reader B can't read or update reader A's profile, sent events or saved lists through the `authenticated` role; reloading `/newsletter` the same day makes no new AI call; editing preferences makes one; signed-out requests to `/newsletter` and `/preferences` redirect to sign-in; the 429 limits trigger.
- Manual checks on the dev project: sign up, confirm email, sign in, edit, reset password, and the Account-A/Account-B test through Supabase's public API with the publishable key (this is also Build 3's access-control test).

### Docs and deploy
- AGENTS.md: stack, structure, commands (local Postgres, migrations), and the Deploy section (backups now via `pg_dump` of the production database; apply migrations before restarting).
- `.env.example`: add the Supabase variables and `DATABASE_URL`; remove `SITE_PASSWORD`.
- Product Page build log: a "What shipped" line.

### Order of work (separate PRs, each leaves `main` working)
1. Postgres: migrations, `app/db.py` on psycopg, tests on local Postgres, data move script for events. Site still behind the pod password.
2. Accounts: Supabase Auth sign-up/sign-in/reset/sign-out, profiles with RLS, existing readers moved, pod password removed, rate limits.
3. Edit preferences page and saved lists.

## Left to the builder

Exact Postgres column types, the migration file split, rate-limit numbers, page wording and styling (matching the current pages), cookie names, how the fake Auth responses are organized, and how the one-time token for a just-signed-up reader is stored.

## Ryan's own tasks

Create the two Supabase projects and copy their keys into `.env` and the server env; connect the email sender (Resend or similar) and its DNS records on whatisupnyc.com; set Supabase's site URL and redirect URLs for password reset.
