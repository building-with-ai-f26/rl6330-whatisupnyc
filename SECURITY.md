# Security audit: whatisupnyc (Build 3)

**Live app:** https://whatisupnyc.com · **Audit date:** 2026-11-23 · **Lane:** Beginner. The Advanced lane's separate dev and production databases, migrations in git, and rollback are also in place. Its CI on every pull request, preview deployments, and automatic deploys are not.

## What kind of app this is

An **AI app with sign-in and a database**. Readers make an account with email and password, say what they like, and get a short list of NYC events that an AI picks from events the server has checked.

- **Sign-in:** Supabase Auth, called only from our FastAPI server. The browser never talks to Supabase; the login lives in `HttpOnly`, `Secure`, `SameSite=Lax` cookies.
- **Database:** Supabase Postgres, with two projects: `whatisupnyc-dev` for building and testing, and `whatisupnyc-prod` for the live site. Schema changes are SQL migrations in `supabase/migrations/`.
- **AI:** a free OpenRouter model, with a paid MiniMax model as backup. Both are called only from the server, with at most 50 requests a day.
- **Email:** confirm and reset emails come from `accounts@whatisupnyc.com` through Resend.
- **Hosting:** a VPS behind Caddy (HTTPS).

## 1. Access controls (app with sign-in): Account B tried to read Account A's data

Every table has Row Level Security on.

- **The event tables:** no policies at all, so Supabase's public API can read or write nothing in them.
- **`saved_for_later` and `event_ratings`:** each reader reads and changes only their own rows (`user_id = auth.uid()` on every action); `event_id` must be a real event, and ratings are 1 to 5. The app accepts only events from the reader's own lists, and ratings only for events that have happened.
- **`profiles`, `sent_events` and `newsletter_issues` (saved lists):** a signed-in reader sees only rows whose `user_id` is their own (`auth.uid()`). They can add only their own sent events and saved lists, and they can change only the preference columns of their own profile (name, interests, extra wishes, days), never the first-look token. They can't add or delete profiles.
- **Inside the server:** reader queries switch to the `authenticated` role with the reader's ID (`db.as_reader`). So even a bug in our code can't hand one reader another's rows.

**Test, run on production on 2026-11-23:** I created two test accounts, A and B. Signed in as B, I used Supabase's public API with the publishable key, the way any outsider could:

| # | What Account B tried | Result |
|---|---|---|
| 1 | Read every profile | `200`, only B's own row |
| 2 | Read A's profile by A's ID | `200`, empty |
| 3 | Rename A | `403` permission denied |
| 4 | Read A's sent events | `200`, empty |
| 5 | Add a sent event to A's account | `403` new row violates row-level security policy |
| 6 | Read the old readers table (emails from before accounts) | `403` permission denied |
| 7 | Read profiles without signing in | `401` |

A's profile was unchanged afterwards, and both test accounts were deleted. Screenshot: [`docs/assets/security-test.png`](docs/assets/security-test.png). The same test passed on dev first. The automated tests prove the same rules on every run: `tests/test_db.py`, `test_reader_*`.

## 2. Secret keys stay on the server

| Searched | For | Result |
|---|---|---|
| Every tracked file | `sb_secret_`, `service_role`, `sk-or-`, `sk-ant-`, `sk-proj-`, `sk_live_`, Resend `re_…` keys | Only placeholders (`.env.example`) and mentions in docs and tests |
| Full git history, every commit | The same patterns | Only the same placeholders and mentions |
| Tracked files and full history | The exact values of all 11 of my real secrets: OpenRouter, Ticketmaster, Tavily, MiniMax, NPS, NYC Events Calendar, Resend, both Supabase secret keys, and both database passwords | Not found |
| The live site's pages (start, sign-in, forgot, reset, confirm, old-link and robots pages; 130 KB) | The patterns and all 11 values | Not found. The only key on the page is PostHog's `phc_` project key, which is public by design |

- **Where the secrets live:** only in `.env` (git-ignored, readable only by me) and `/etc/whatisupnyc/env` on the server (root-only).
- **The web app's key:** only Supabase's publishable key.
- **The Supabase secret key:** never on the server. Only the one-time reader move script used it, from my Mac, for one command.
- **Old-style keys:** Supabase's legacy JWT keys (`anon`, `service_role`) are disabled on both projects. Both now answer `401`.

**Found and fixed:** while connecting the dev project, the agent printed parts of two secrets into the session log:
1. **The dev database password.** Its masking stopped at the first `@`, and the password contained one. I reset the password.
2. **The legacy dev `service_role` key.** It printed while the agent was listing the keys. I disabled the legacy keys on both projects.

Neither secret reached git or the live site. See the `AGENTS.md` rule this produced.

## 3. File storage

The app stores no files: no Supabase Storage buckets, and no uploads. Event pictures are links to the event's own feed or page, `https` only, loaded with `referrerpolicy="no-referrer"` so the image host doesn't learn which reader's page asked.

**Public share page:** `/e/<id>` shows one event (picture, date, venue, link) for sharing with friends. It shows only events the site recommends (event numbers can be counted through), and nothing about who shared it.

**Emailing a list:** only to the signed-in reader's own address from Supabase (any address in the form is ignored), at most 3 a day per reader, and only when `RESEND_API_KEY` is set on the server.

## 4. Protecting production from the agent

- **"Ask me first" list** in `AGENTS.md`: deleting data or changing the database; keys, secrets and permissions; anything that costs money; deploys.
- **The agent works against the dev database.** Production changes happen only through the written deploy steps, which start with backups:
  - `pg_dump` of the production database to my Mac: `~/backups/whatisupnyc-2026-11-23-before-accounts.sql`, all 7 tables. Supabase's free plan has no automatic backups.
  - The server's code and settings, copied to `/root/backups/whatisupnyc-2026-11-23-pre-accounts`.
- **Practice runs:** both production database moves were rehearsed on dev with a copy of the production data first, and their row counts were compared.
- **Rollback:** copy the backup's code back and restart. Restore the `pg_dump` for data. Steps are in `AGENTS.md`.

## 5. Spending caps and rate limits

- **AI spend:** at most 50 AI requests a day, counted in the database. The primary model is free. **MiniMax has no hard monthly cap**, so that cap is the open item in "Remaining issues".
- **Per-visitor rate limits** on every route that calls the AI or writes to the database (`app/limits.py`). Over the limit returns `429`, or a page that looks the same where showing a lockout would help an attacker.

| Route | Per address | Per account |
|---|---|---|
| Sign-up | 30 an hour, counted once the form is complete | |
| Sign-in | 60 per 15 min | Wrong passwords: 10 per 15 min per account and address; 50 per 15 min per account overall |
| Forgot password | 10 an hour | 3 emails an hour; past that the page looks the same and nothing is sent |
| Pasted posts | 30 an hour | |
| Newsletter (calls the AI only for a new list) | 60 an hour | 10 new lists an hour per reader; saved lists don't count |

- **Why the per-address limits are generous:** a classroom on campus Wi-Fi shares one public address.
- **How addresses are counted:** the address comes from Caddy's `X-Forwarded-For` header, trusted only from `127.0.0.1`. IPv6 visitors count by their /64 block.
- **Behind ours:** Supabase's own email limit (30 an hour) and the 50-a-day AI limit.

## 6. Dependency audit

`pip-audit` on the production dependencies (`uv export --no-dev`):
- **Found:** one known vulnerability, `soupsieve 3.0.4` (CVE-2026-126190, slow CSS selectors on deeply nested HTML). The app never calls Beautiful Soup's selector functions, so it couldn't be reached.
- **Fixed anyway:** upgraded to `soupsieve 4.1.3`.
- **Result:** no known vulnerabilities.

## 7. Agent security review

- **A fresh reviewer** on the most capable model read the whole accounts branch, re-ran the tests, and checked that the reader role doesn't leak to the next request reusing a database connection. It found nothing critical.
- **An automated security reviewer** checked every commit.

Everything they found was fixed with a test that failed first, or kept as a recorded decision:

| Found | Fix |
|---|---|
| **Pre-account takeover:** someone signs up with your email and their own password, and when you click the confirm link, their password still works | Confirming asks for the password, sets it, and ends every other login. If the password can't be set, the account gets a random one nobody knows before everyone is signed out |
| A stranger's 24-hour "first look" at a new account kept working after the owner confirmed | Using a confirm or reset link ends it |
| Mail scanners that open links before the reader could use up confirm and reset links | Links open a page with a button; only the button's `POST` uses the token |
| A renewed login could be lost during the 40-second AI call, signing the reader out | The new cookies are stored with a quick redirect before the AI is called |
| Per-account limits let a stranger lock you out of your own account | Only wrong passwords count, per account *and* address; reset limits are silent |
| Wrong guesses sent at the same moment slipped under the limit | A try is reserved before Supabase is asked, then given back if the password was right, and the limiter takes a lock |
| The rate limiter could be made to do too much work or use too much memory, and IPv6 addresses could be rotated | A cap on tracked visitors, clean-up at most once a minute, IPv6 counted by /64 |
| Email-link tokens could reach analytics | PostHog hides `token_hash` in every event; recording is off on the list, reset and confirm pages |
| The deploy notes would have broken production | A step-by-step one-time deploy order in `AGENTS.md` |

Also checked, with tests:
- every form refuses posts from other sites (`Origin`/`Referer` check), including sign-in and sign-out;
- the forgot-password page says the same thing for any email;
- user-typed text is escaped on every page;
- the automatic API docs pages are off;
- the password never appears in a page sent back after an error.

### Five attacks tried against the app

1. **Read another reader's data through Supabase's public API** (signed in as B, going after A): blocked by Row Level Security (section 1).
2. **Take over an account by signing up first with the owner's email**, then wait for them to confirm: the owner's confirm sets their password and signs the attacker out. Tested in `test_confirming_sets_the_password_and_ends_other_logins`, and the confirm flow was checked on the live site.
3. **Lock the owner out** with wrong passwords from another address: the owner still signs in from their own (`test_a_strangers_wrong_guesses_do_not_lock_out_the_owner`).
4. **Submit forms from another website**, using the reader's cookies: `403` on every form (`test_every_form_refuses_other_sites`).
5. **Find secrets in the live site's code:** none found (section 2).

## Remaining issues

- **MiniMax has no hard monthly spending cap** (its console doesn't offer one). The 50-requests-a-day limit bounds it to about $0.20 a day.
- **The account-existence hint:** sign-up shows whether an email already has an account. A new email goes straight to its first list, while an existing one gets "check your email". The design needs the instant first list, so this is accepted. Sign-ups are rate limited, and each probe creates only an unconfirmed account.
- **Clearing every device:** "Sign out" ends only that device's login. Readers sign out everywhere by resetting their password.
- **Email-link tokens in the server log:** the access log records them, and only root can read it.
- **Small fixes left:** a failed login renewal doesn't clear the cookies. (Fixed since the first audit: reloading the list no longer calls the AI, because lists are saved; an account with no preferences is sent to fill them in; "Mark as sent", whose redirect could drop a renewed login, is gone.)
