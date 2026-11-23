# Reader Accounts (PR 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the shared site password with email-and-password reader accounts (Supabase Auth), with each reader's preferences in a `profiles` table that Row Level Security keeps private, per-visitor rate limits, and every existing reader moved to an account.

**Architecture:** FastAPI talks to Supabase Auth's HTTP API with `httpx` (`app/supabase_auth.py`); the browser never talks to Supabase. The login lives in HttpOnly cookies. Reader queries in `app/db.py` run inside a transaction that switches to the `authenticated` role with the reader's user ID, so the database's own policies stop one reader reaching another's rows even if app code has a bug. Email links (confirm, reset password) come back to our server with a `token_hash` that the server exchanges for a login.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3 + psycopg_pool, httpx, Supabase Auth (GoTrue HTTP API), Supabase Postgres, local Homebrew Postgres for tests.

**Spec:** `docs/superpowers/specs/2026-11-21-supabase-accounts-design.md` (this is PR 2 of its "Order of work"). PR 1 (`docs/superpowers/plans/2026-11-21-postgres-move.md`) is merged and deployed.

## Global Constraints

- Server-side only: no Supabase code or keys in the browser. No new Supabase library; `httpx` only.
- Cookies: `HttpOnly`, `Secure`, `SameSite=Lax`. Names: `wnyc_access`, `wnyc_refresh`, `wnyc_first_look`.
- Keys: `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` (`sb_publishable_…`) in the web app; `SUPABASE_SECRET_KEY` (`sb_secret_…`) only in the one-time reader move script, never read by `app/`. All in `.env` / `/etc/whatisupnyc/env` and listed in `.env.example`. `SITE_PASSWORD` is removed.
- Keep `auth.sent_from_this_site` on every form post. Remove the pod password check.
- Row Level Security on every table. `profiles` and `sent_events` policies allow the `authenticated` role only rows where `user_id = auth.uid()`.
- Sign-up with an email that already has an account shows the same message as a new sign-up that needs confirming ("Check your email to confirm, or sign in / reset your password if you already have an account") and saves nothing.
- A brand-new account sees its list right away through a random one-time token in `wnyc_first_look`, valid 24 hours; banner: "Confirm your email to come back and edit your picks."
- Rate limits return `429`. Client IP comes from `X-Forwarded-For` only when the request's own address is `127.0.0.1` (Caddy). Numbers (builder's call): sign-up 5/hour, sign-in 10 per 15 minutes, password reset 3/hour, pasted posts 10/hour, newsletter page 20/hour, per IP.
- The 50-a-day AI limit stays. Tests stay offline: Supabase Auth is faked; the database is local Postgres with a stand-in `auth` schema.
- Old `/newsletter/{token}` links show a page pointing to sign-in and "Forgot password" (status 410).
- Out of scope (PR 3): editing preferences, saved lists, dropping the `old_*` tables. Out of scope entirely: changing email, deleting accounts, other sign-in methods.
- Matching AGENTS.md: small commits, page wording plain and matching current pages, `.env.example` updated.

## Review Focus

1. **Expired access cookie with a good refresh cookie:** the reader stays signed in and gets new cookies, rather than being sent to sign-in every hour. Test in Task 4 (`test_expired_access_token_refreshes_and_sets_new_cookies`).
2. **Supabase Auth down or slow (timeout, 5xx):** sign-up and sign-in show "Sign-in is down right now, please try again in a little while" with status 503 and keep what was typed (not the password), rather than a 500 page. Test in Task 4 (`test_auth_down_keeps_form_and_says_so`).
3. **Email typed with different case or spaces** (`" Ana@X.test"`): one account, and the reader move treats `ana@x.test` and `Ana@x.test` as the same reader. Tests in Task 5 (`test_emails_differing_only_in_case_are_one_reader`) and Task 4 (`test_sign_up_trims_email`).
4. **A signed-in account with no profile row** (profile insert failed, or an account made outside the site): `/newsletter` shows a page with a link back to the start instead of crashing. Test in Task 4 (`test_signed_in_without_profile_gets_pointed_to_start`).
5. **An email link opened twice, or prefetched by a mail scanner:** the confirm and reset links open a page with a button; only the button's POST uses the token, so a scanner's GET can't spend it. Test in Task 4 (`test_confirm_link_get_does_not_spend_the_token`).

---

### Task 1: Accounts tables with Row Level Security, and reader queries that run as the reader

**Files:**
- Create: `supabase/migrations/20261122000000_reader_accounts.sql`
- Create: `tests/fixtures/supabase_auth_stub.sql`
- Modify: `tests/conftest.py` (apply the stub before migrations; truncate new tables and `auth.users`)
- Modify: `app/db.py` (TABLES, reader functions), `app/models.py` (`Subscriber`)
- Delete: `scripts/move_to_supabase.py`, `tests/test_move_to_supabase.py`, `tests/fixtures/old_sqlite_schema.sql` (the one-time SQLite move is done; git history keeps them)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces:
  - `Subscriber(id: str, name: str, interests: str, look_prompt: str, days: list[str])` in `app/models.py` (`id` is the account's user ID as text; `email` removed: email lives only in Supabase Auth).
  - `db.create_profile(conn, user_id: str, name: str, interests: str, look_prompt: str, days: list[str], now: datetime) -> str | None`: inserts as the owner role, with a new `_new_token()` as `first_look_token` and `first_look_until = now + 24h`. Returns that token, or `None` when a profile for `user_id` already exists (`ON CONFLICT DO NOTHING`, nothing changed).
  - `db.get_profile(conn, user_id: str) -> Subscriber | None`: runs through `as_reader`.
  - `db.profile_for_first_look(conn, token: str, now: datetime) -> Subscriber | None`: owner role; `None` when unknown or `first_look_until <= now`.
  - `db.mark_sent(conn, user_id: str, event_ids: list[int]) -> None` and `db.sent_event_ids(conn, user_id: str) -> set[int]`: both through `as_reader`; same names as today, user ID instead of subscriber ID.
  - `db.as_reader(conn, user_id: str)`: context manager yielding a connection inside a transaction after `set local role authenticated` and `select set_config('request.jwt.claims', <json {"sub": user_id, "role": "authenticated"}>, true)`.
  - `db.TABLES` lists every public table for test truncation: `events, dropped_events, old_subscribers, old_sent_events, profiles, sent_events, pastes, ai_requests, waiting_pastes`.
  - Removed: `save_subscriber`, `get_subscriber`, `subscriber_token`, `find_subscriber_by_token`.

- [ ] **Step 1: Write the auth stand-in for local Postgres** in `tests/fixtures/supabase_auth_stub.sql`: `drop schema if exists auth cascade; create schema auth; create table auth.users (id uuid primary key default gen_random_uuid(), email text);` a function `auth.uid() returns uuid` reading `(current_setting('request.jwt.claims', true)::json ->> 'sub')::uuid`; create roles `anon` and `authenticated` only if missing (a `DO` block, since roles are shared across the local cluster); `grant usage on schema auth to authenticated; grant execute on function auth.uid() to authenticated`. Conftest runs it after recreating `public` and before the migrations, and truncates `auth.users cascade` along with `db.TABLES`.

- [ ] **Step 2: Write the failing tests** in `tests/test_db.py`. Helper `make_user(conn) -> str` inserts into `auth.users` and returns the id as text.

```python
def test_reader_sees_only_their_own_profile(conn):
    a, b = make_user(conn), make_user(conn)
    db.create_profile(conn, a, "Ana", "jazz", "", ["Friday"], NOW)
    db.create_profile(conn, b, "Bo", "comedy", "", ["Monday"], NOW)
    with db.as_reader(conn, a) as c:
        assert [r["name"] for r in c.execute("select name from profiles")] == ["Ana"]
        assert c.execute("select 1 from profiles where user_id = %s", (b,)).fetchone() is None
    assert db.get_profile(conn, b).name == "Bo"

def test_reader_cannot_read_or_add_another_readers_sent_events(conn):
    a, b = make_user(conn), make_user(conn)
    for user in (a, b):
        db.create_profile(conn, user, "R", "jazz", "", ["Friday"], NOW)
    db.mark_sent(conn, b, [7])
    assert db.sent_event_ids(conn, a) == set()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # RLS "new row violates row-level security policy"
        with db.as_reader(conn, a) as c:
            c.execute("insert into sent_events (user_id, event_id) values (%s, 8)", (b,))
    assert db.sent_event_ids(conn, b) == {7}

def test_reader_cannot_insert_or_delete_profiles(conn): ...  # insert and delete as_reader raise InsufficientPrivilege
def test_create_profile_twice_returns_none_and_keeps_the_first(conn): ...
def test_first_look_token_works_for_24_hours_only(conn): ...  # NOW + 23h -> profile; NOW + 24h -> None; unknown token -> None
def test_first_look_token_is_long_and_random(conn): ...  # len >= 43, two profiles get different tokens
def test_every_table_has_row_level_security(conn): ...  # existing test: still passes with the new tables
```

Remove the old subscriber tests (`save_subscriber`, token lookups) that test removed functions.

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/test_db.py -q`
Expected: FAIL (`create_profile` not defined / `profiles` does not exist)

- [ ] **Step 4: Write the migration** `supabase/migrations/20261122000000_reader_accounts.sql`:
  - `alter table subscribers rename to old_subscribers; alter table sent_events rename to old_sent_events;` (kept read-only for the reader move and as a backup; PR 3 drops them; they keep their RLS with no policies).
  - `profiles (user_id uuid primary key references auth.users (id) on delete cascade, name text not null, interests text not null, look_prompt text not null, days text[] not null, first_look_token text unique, first_look_until timestamptz)`.
  - `sent_events (user_id uuid not null references profiles (user_id) on delete cascade, event_id bigint not null, primary key (user_id, event_id))`.
  - Enable RLS on both. Grants: `grant select on profiles to authenticated; grant select, insert on sent_events to authenticated;` (no update: editing is PR 3; no grants to `anon`).
  - Policies: `profiles` select `using (user_id = auth.uid())`; `sent_events` select `using (user_id = auth.uid())` and insert `with check (user_id = auth.uid())`. Use `(select auth.uid())` in the policies, Supabase's recommended form.
  - A comment at the top saying why the server switches role (defense in depth) and that `postgres` is a member of `authenticated` on Supabase (checked on dev 2026-11-22).

- [ ] **Step 5: Implement the `app/db.py` and `app/models.py` changes** per the Interfaces block. `get_profile` and `sent_event_ids` read through `as_reader`; `mark_sent` inserts through it with `ON CONFLICT DO NOTHING`.

- [ ] **Step 6: Run the database tests**

Run: `uv run pytest tests/test_db.py -q`
Expected: PASS. (`tests/test_main.py` fails until Task 4; that's expected on this branch only.)

- [ ] **Step 7: Commit**

```bash
git add supabase/migrations tests/fixtures/supabase_auth_stub.sql tests/conftest.py tests/test_db.py app/db.py app/models.py
git rm scripts/move_to_supabase.py tests/test_move_to_supabase.py tests/fixtures/old_sqlite_schema.sql
git commit -m "Add reader profiles that only their owner can read"
```

---

### Task 2: Supabase Auth client

**Files:**
- Create: `app/supabase_auth.py`
- Create: `tests/fixtures/supabase_auth/` (saved JSON replies: `signup_new.json`, `signup_existing.json` (a user with `"identities": []`), `token_ok.json`, `token_invalid.json` (400, `"error_code": "invalid_credentials"`), `token_unconfirmed.json` (400, `"error_code": "email_not_confirmed"`), `user_ok.json`, `user_expired.json` (403, `"error_code": "bad_jwt"`), `verify_ok.json`, `verify_expired.json` (403, `"error_code": "otp_expired"`), `weak_password.json` (422, `"error_code": "weak_password"`))
- Test: `tests/test_supabase_auth.py`

**Interfaces:**
- Produces, in `app/supabase_auth.py`:
  - `@dataclass AuthUser(id: str, email: str)`; `@dataclass Session(access_token: str, refresh_token: str, user: AuthUser)`.
  - `class AuthRefused(Exception)` with `.code: str` (Supabase's `error_code`, e.g. `invalid_credentials`, `email_not_confirmed`, `weak_password`, `otp_expired`); `class AuthUnavailable(Exception)` for network errors, timeouts and 5xx.
  - `class SupabaseAuth(http: httpx.Client, url: str, publishable_key: str)` with:
    - `sign_up(email, password) -> AuthUser | None`: `POST /auth/v1/signup`; returns `None` when the reply's user has no identities (email already registered).
    - `sign_in(email, password) -> Session`: `POST /auth/v1/token?grant_type=password`.
    - `refresh(refresh_token) -> Session`: `POST /auth/v1/token?grant_type=refresh_token`.
    - `get_user(access_token) -> AuthUser | None`: `GET /auth/v1/user`; `None` on 401/403.
    - `send_reset(email) -> None`: `POST /auth/v1/recover`.
    - `verify(token_hash, kind: str) -> Session`: `POST /auth/v1/verify` with `{"type": kind, "token_hash": ...}`; `kind` is `"email"` or `"recovery"`.
    - `set_password(access_token, password) -> None`: `PUT /auth/v1/user`.
    - `sign_out(access_token) -> None`: `POST /auth/v1/logout`; ignores 401/403.
  - `auth_from_env(http) -> SupabaseAuth` reading `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`.
  - Every call sends header `apikey: <publishable key>`; calls for a signed-in reader add `Authorization: Bearer <access token>`. Timeout 10 seconds.

- [ ] **Step 1: Write the fixtures** in the shape of Supabase Auth's replies (session: `access_token`, `refresh_token`, `expires_in`, `user: {id, email, identities: [...]}`; errors: `{"code": <status>, "error_code": ..., "msg": ...}`). The Task 6 dev check confirms the real shapes match.

- [ ] **Step 2: Write the failing tests** with `httpx.MockTransport` serving the fixtures:

```python
def test_sign_up_returns_the_new_user(): ...           # AuthUser id/email from signup_new.json; request body has email and password
def test_sign_up_existing_email_returns_none(): ...    # signup_existing.json
def test_sign_in_returns_session(): ...
def test_wrong_password_raises_refused_with_code(): ...  # AuthRefused.code == "invalid_credentials"
def test_unconfirmed_email_raises_refused_with_code(): ...
def test_expired_access_token_gives_no_user(): ...     # get_user -> None
def test_verify_sends_token_hash_and_type(): ...        # body == {"type": "recovery", "token_hash": "abc"}
def test_server_error_and_timeout_raise_unavailable(): ...  # 503 reply, and httpx.ReadTimeout
def test_every_call_sends_the_publishable_key_not_a_secret(): ...  # apikey header == "sb_publishable_test"
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/test_supabase_auth.py -q`
Expected: FAIL (module not found)

- [ ] **Step 4: Implement `app/supabase_auth.py`** per the Interfaces block. A 4xx with a JSON `error_code` becomes `AuthRefused(code)`; anything else non-2xx, plus `httpx.HTTPError`, becomes `AuthUnavailable`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_supabase_auth.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/supabase_auth.py tests/test_supabase_auth.py tests/fixtures/supabase_auth
git commit -m "Add a small Supabase Auth client that runs only on the server"
```

---

### Task 3: Per-visitor rate limits

**Files:**
- Create: `app/limits.py`
- Test: `tests/test_limits.py`

**Interfaces:**
- Produces:
  - `LIMITS: dict[str, tuple[int, int]] = {"sign_up": (5, 3600), "sign_in": (10, 900), "reset": (3, 3600), "paste": (10, 3600), "newsletter": (20, 3600)}` (count, seconds).
  - `class RateLimiter(limits: dict[str, tuple[int, int]], clock: Callable[[], float] = time.monotonic)` with `allow(bucket: str, key: str) -> bool` (sliding window; a refused try does not count; memory per key is bounded by the window).
  - `client_ip(request: Request) -> str`: the last address in `X-Forwarded-For` when `request.client.host == "127.0.0.1"`, otherwise `request.client.host`.

- [ ] **Step 1: Write the failing tests**

```python
def test_allows_up_to_the_limit_then_refuses(): ...   # 5 allowed, 6th refused, other key still allowed
def test_window_slides(): ...                          # fake clock: after 3600 s the oldest try expires
def test_buckets_are_separate(): ...
def test_forwarded_header_trusted_only_from_caddy(): ...
    # client 127.0.0.1 + "X-Forwarded-For: 1.2.3.4, 5.6.7.8" -> "5.6.7.8"
    # client 9.9.9.9 + same header -> "9.9.9.9"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_limits.py -q`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `app/limits.py`** with a `dict[(bucket, key)] -> deque[float]`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_limits.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/limits.py tests/test_limits.py
git commit -m "Add per-visitor rate limits"
```

---

### Task 4: The site opens to everyone; accounts replace the site password

**Files:**
- Modify: `app/main.py`, `app/auth.py` (keep only `sent_from_this_site` and `SAFE_METHODS`)
- Modify: `app/templates/index.html` (password step; "Sign in" link in the header), `app/templates/view.html` (no email, no private-link sentence; first-look banner; "Sign out" form; "Mark as sent" posts to `/newsletter/sent`)
- Create: `app/templates/signin.html`, `forgot.html`, `reset.html`, `confirm.html`, `notice.html` (one message plus optional links; used for "check your email", old links, expired links, missing profile)
- Modify: `tests/fakes.py` (add `FakeAuth`), `tests/test_main.py`, `tests/test_auth.py`
- Test: `tests/test_accounts.py` (new; sign-up, sign-in, reset, confirm, sign-out, limits)

**Interfaces:**
- Consumes: Task 1 `db.create_profile`, `db.get_profile`, `db.profile_for_first_look`, `db.mark_sent`, `db.sent_event_ids`; Task 2 `SupabaseAuth`, `AuthUser`, `Session`, `AuthRefused`, `AuthUnavailable`, `auth_from_env`; Task 3 `RateLimiter`, `LIMITS`, `client_ip`.
- Produces:
  - `AppDeps` loses `site_password`, gains `auth: SupabaseAuth` and `limiter: RateLimiter` (default `RateLimiter(LIMITS)`).
  - `tests/fakes.py: FakeAuth(conn)`: same methods as `SupabaseAuth`; keeps accounts in memory (email → password, confirmed flag, user id) and inserts each new user into the local `auth.users` so profiles' foreign key holds; issues tokens like `access-<n>`; has switches `down: bool` (every call raises `AuthUnavailable`) and `expire(access_token)`; records `resets_sent` and `signed_out`.
  - Routes: `GET /`, `POST /subscribers` (now with `password`), `GET|POST /signin`, `POST /signout`, `GET /newsletter`, `POST /newsletter/sent`, `GET /newsletter/{token}` (410 notice), `GET|POST /forgot`, `GET|POST /reset`, `GET|POST /confirm`, `POST /paste`.

- [ ] **Step 1: Switch the test client to the new world** in `tests/test_main.py`: `client_for(conn, sites, ai=None, auth=None, posthog_key="")` builds `AppDeps(..., auth=auth or FakeAuth(conn), limiter=RateLimiter(LIMITS))`, uses `base_url="https://testserver"` (so `Secure` cookies are sent), sets `Origin: https://testserver`, and drops `client.auth`. `GOOD` gains `"password": "long-enough-1"`. Replace `save_ana` with `sign_up_ana(client) -> None` (posts `GOOD`) and `signed_in_ana(client, fake_auth) -> None` (marks Ana confirmed in the fake, posts `/signin`). Update existing tests that used tokens to use these. Delete the site-password tests in `tests/test_auth.py`; keep the other-site form tests, now with no password.

- [ ] **Step 2: Write the failing tests** in `tests/test_accounts.py`:

```python
def test_site_opens_without_a_password(...): ...        # GET / -> 200, no WWW-Authenticate header
def test_sign_up_creates_account_and_profile_and_shows_list_now(...):
    # POST /subscribers -> 303 to /newsletter; wnyc_first_look cookie set (HttpOnly, Secure, SameSite=Lax);
    # GET /newsletter -> 200, picks shown, banner "Confirm your email to come back and edit your picks."
def test_sign_up_with_existing_email_saves_nothing_and_says_check_email(...):
    # second sign-up with the same email (other password and interests): 200, the generic message, no cookie,
    # and the first profile's interests unchanged
def test_sign_up_trims_email(...): ...                  # " Ana@X.test " -> FakeAuth saw "Ana@X.test"
def test_short_password_rejected_and_form_kept(...): ...  # < 8 characters -> 400, typed fields kept, password field empty
def test_weak_password_from_supabase_shows_message(...): ...  # AuthRefused("weak_password") -> 400
def test_auth_down_keeps_form_and_says_so(...): ...     # FakeAuth.down -> 503 "Sign-in is down right now..."
def test_sign_in_sets_cookies_and_opens_newsletter(...): ...
def test_wrong_password_says_so_without_saying_which(...): ...  # "Wrong email or password."
def test_unconfirmed_email_asks_to_confirm_first(...): ...
def test_expired_access_token_refreshes_and_sets_new_cookies(...): ...
def test_signed_out_newsletter_redirects_to_sign_in(...): ...  # GET /newsletter with no cookies -> 303 /signin
def test_first_look_cookie_expires_after_24_hours(...): ...    # clock + 24h -> 303 /signin
def test_signed_in_without_profile_gets_pointed_to_start(...): ...  # notice with link to /
def test_old_token_link_points_to_sign_in(...): ...            # GET /newsletter/abc -> 410, links to /signin and /forgot
def test_sign_out_clears_cookies_and_tells_supabase(...): ...  # POST /signout -> 303 /; cookies deleted; FakeAuth.signed_out
def test_forgot_says_the_same_thing_for_any_email(...): ...    # known and unknown email -> same page text
def test_reset_link_sets_new_password_and_signs_in(...): ...   # GET /reset?token_hash=t&type=recovery shows form; POST sets it
def test_expired_reset_link_offers_a_new_one(...): ...         # AuthRefused("otp_expired") -> notice linking /forgot
def test_confirm_link_get_does_not_spend_the_token(...): ...   # GET /confirm shows a button; FakeAuth.verify not called
def test_confirm_post_signs_in_and_opens_newsletter(...): ...
def test_mark_sent_uses_the_signed_in_reader(...): ...         # POST /newsletter/sent, next list skips those events
def test_rate_limits_return_429(...):  # parametrize over (path, form, bucket): sign-up, sign-in, forgot, paste, newsletter
    # one past the LIMITS count -> 429; the limiter is the app's own, so reuse its LIMITS numbers
def test_no_page_mentions_a_secret_key(...): ...               # rendered pages never contain "sb_secret_" or "SUPABASE"
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/test_accounts.py tests/test_main.py tests/test_auth.py -q`
Expected: FAIL

- [ ] **Step 4: Implement `app/main.py`.** Decisions the tests don't pin:
  - Replace the `require_password` middleware with one that returns `403 "Forms must be sent from this site."` when `not auth.sent_from_this_site(request)`.
  - One helper `current_reader(request) -> tuple[Subscriber | None, Session | None, bool]` (profile, refreshed session to store, whether it came from a first look). Order: `wnyc_access` → `get_user`; if `None` and `wnyc_refresh` → `refresh` (on `AuthRefused`, treat as signed out); else `wnyc_first_look` → `profile_for_first_look`. A refreshed session is written back on the response.
  - `set_session_cookies(response, session)` and `clear_session_cookies(response)`: access and refresh cookies `max_age` 30 days; first-look cookie `max_age` 24 hours. A sign-in or confirm also deletes `wnyc_first_look`.
  - Sign-up: `sign_up` returning `None`, or `create_profile` returning `None`, both lead to the same generic "check your email" notice with nothing saved and no cookie.
  - Password minimum 8 characters, checked before calling Supabase. Never echo the password back into the form.
  - `AuthUnavailable` on any account page → that page with "Sign-in is down right now, please try again in a little while." and status 503.
  - Rate-limit checks go first in each limited route, keyed by `client_ip(request)`; `GET /newsletter` counts too, because it calls the AI.
  - The start page's header shows "Sign in" (or "My list" when `current_reader` finds a profile; cheap, as it's one Auth call).

- [ ] **Step 5: Update the templates.** The password step goes right after the email step in the existing step flow (`data-name="password"`, `type="password"`, `autocomplete="new-password"`, `minlength="8"`), and the start page's step-tracking script includes the new step without sending typed text (the existing `test_start_step_tracking_sends_no_typed_text` covers it). New pages reuse `partials/head.html`, `partials/base.css`, the logo, and the look of `view.html`. Remove "Copy this list" only if it breaks; otherwise leave it for PR 3.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS, no tests skipped

- [ ] **Step 7: Commit**

```bash
git add app tests
git commit -m "Open the site to everyone and sign readers in with email and password"
```

---

### Task 5: Move existing readers to accounts

**Files:**
- Create: `scripts/move_readers_to_accounts.py`
- Test: `tests/test_move_readers.py`

**Interfaces:**
- Consumes: Task 1 tables (`old_subscribers`, `old_sent_events`, `profiles`, `sent_events`).
- Produces: `move_readers(conn, http: httpx.Client, url: str, secret_key: str) -> dict[str, int]` returning `{"readers": n_emails, "accounts_created": n, "accounts_found": n, "profiles": n, "sent_events": n}`. CLI: `uv run python -m scripts.move_readers_to_accounts` reading `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`.

- [ ] **Step 1: Write the failing tests** against local Postgres, with `httpx.MockTransport` standing in for Supabase's admin API (`GET /auth/v1/admin/users?page=&per_page=` and `POST /auth/v1/admin/users`); the fake's `POST` inserts into the local `auth.users` and returns `{"id": ..., "email": ...}`:

```python
def test_each_reader_gets_a_confirmed_account_and_profile(conn): ...
    # POST body has email_confirm: True and no password; profile has the row's name, interests, look_prompt, days
def test_emails_differing_only_in_case_are_one_reader(conn): ...
    # rows "ana@x.test" (id 1) and " Ana@X.test" (id 4): one account; profile from row 4 (newest);
    # sent_events = union of both rows' old_sent_events
def test_existing_account_is_reused_not_duplicated(conn): ...  # admin list already has the email -> no POST
def test_running_twice_changes_nothing(conn): ...             # second run: profiles and sent_events counts unchanged
def test_sent_events_for_missing_events_are_kept(conn): ...    # no foreign key to events, same as before
def test_requests_use_the_secret_key(conn): ...                # apikey and Bearer header == the secret key
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_move_readers.py -q`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement the script.** Group `old_subscribers` by `lower(trim(email))`; newest `id` wins. Account emails are the trimmed, lowercased form. List existing users once (page through until a short page), create the missing ones, insert profiles with `ON CONFLICT DO NOTHING`, and insert merged sent events with `ON CONFLICT DO NOTHING`, all as the owner role in one transaction after the accounts exist. Print the counts. Never print the secret key.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_move_readers.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/move_readers_to_accounts.py tests/test_move_readers.py
git commit -m "Add the one-time script that gives existing readers accounts"
```

---

### Task 6: Settings, docs, and the dev check

**Files:**
- Modify: `.env.example` (add `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY` with a note that only the move script reads it; remove `SITE_PASSWORD`)
- Modify: `AGENTS.md` (stack: Supabase Auth; structure: `app/supabase_auth.py`, `app/limits.py`, the new script, `app/auth.py` now only checks forms come from this site; Run locally: no site password, sign up instead; Deploy: the PR 2 order below)
- Modify: `docs/index.html` (Build 3 entry "What shipped" line, after the production deploy)

- [ ] **Step 1: Update `.env.example` and `AGENTS.md`.**

- [ ] **Step 2: Builder setup on the dev project (Ryan, with these exact values):**
  - Authentication → Sign In / Providers → Email: "Confirm email" on; minimum password length 8.
  - Authentication → URL Configuration: Site URL `http://127.0.0.1:8000` on dev, `https://whatisupnyc.com` on production.
  - Authentication → Emails → Templates: "Confirm signup" link `{{ .SiteURL }}/confirm?token_hash={{ .TokenHash }}&type=email`; "Reset password" link `{{ .SiteURL }}/reset?token_hash={{ .TokenHash }}&type=recovery`.
  - Copy `SUPABASE_URL`, the publishable key and the secret key into `.env`.
  - Dev can use Supabase's built-in sender (it mails only the project's team, 2 an hour); production needs the custom sender (Resend) first.

- [ ] **Step 3: Apply the migration to dev and run the reader move there**

Run: `supabase db push --db-url "$DATABASE_URL"` then `uv run python -m scripts.move_readers_to_accounts`
Expected: `readers: 11, profiles: 11` (15 rows, 4 emails seen twice in the production copy now on dev; check the duplicate count first with `select count(distinct lower(trim(email))) from old_subscribers`), and the same output on a second run.

- [ ] **Step 4: Manual check on dev, recorded in the PR:** sign up with Ryan's email → list shows right away with the banner → confirm email from the inbox → sign in → sign out → forgot password → reset → sign in with the new password; sign-up again with the same email shows the generic message; the 6th sign-up within an hour from one IP gets 429. Access-control test (also Build 3's): sign in as A and B through Supabase's public API (`POST {SUPABASE_URL}/auth/v1/token?grant_type=password` with the publishable key), then with A's access token `GET {SUPABASE_URL}/rest/v1/profiles?select=*` returns only A's row and `PATCH ... ?user_id=eq.<B>` changes nothing. Screenshot it.

- [ ] **Step 5: Commit**

```bash
git add .env.example AGENTS.md
git commit -m "Document reader accounts, the new settings, and the deploy order"
```

- [ ] **Step 6: Production deploy (each command on the Ask me first list; after the PR is merged):** production email sender and templates set up first (Ryan); `pg_dump` backup; stop the app and timer; `supabase db push --db-url "$PROD_DATABASE_URL"`; `git pull` and `uv sync`; in `/etc/whatisupnyc/env` add `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`, remove `SITE_PASSWORD`; run the reader move from the Mac against production (production `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY` set only for that command); restart; check sign-up and sign-in on whatisupnyc.com; then the build log line.
