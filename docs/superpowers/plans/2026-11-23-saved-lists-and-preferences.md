# Saved Lists and Editable Preferences (PR 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reader's list is made by the AI at most once per New York day (or right after they change their preferences) and saved, so opening it, signing in, confirming or resetting a password shows it instantly; signed-in readers can edit their preferences.

**Architecture:** A `newsletter_issues` table holds each list made (the picks and their one-line reasons). `app/saved_lists.py` decides whether today's saved list can be shown or a new one must be made, and saves new lists together with their sent events in one reader-scoped transaction. `/preferences` edits the profile through the reader's own Row Level Security policy. Pages that may wait on the AI show the existing "Making your list…" screen.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3, Supabase Postgres with RLS, Jinja templates; tests on local Postgres with the auth stand-in from PR 2.

**Spec:** `docs/superpowers/specs/2026-11-21-supabase-accounts-design.md` ("Saved lists", "Edit preferences", Order of work item 3). PR 2's plan: `docs/superpowers/plans/2026-11-22-accounts-sign-in.md`.

## Global Constraints

- A new list is made (one AI call) only when the reader has no list from today, New York time, or saved preferences after their latest list was made. Its events are written to `sent_events` when it is made.
- Saved lists replace "Copy this list" and "Mark as sent" on the reader's page; `POST /newsletter/sent` is removed.
- A failed AI call shows today's "AI is busy" message; the reader's previous saved list, if any, still shows.
- `/preferences`: a plain form prefilled with interests, extra wishes (`look_prompt`), days and name. Saving updates the profile and returns to the newsletter. Signed-in readers only; a first-look (just signed up) reader is sent to sign in.
- RLS on every table. `newsletter_issues`: the `authenticated` role may select and insert only rows where `user_id = auth.uid()`. `profiles`: the `authenticated` role may update only its own row, only the columns `name, interests, look_prompt, days, preferences_changed_at`.
- Drop `old_subscribers` and `old_sent_events` (every reader has moved, on dev and production).
- The per-account `newsletter_account` limit (10 an hour) counts only loads that make a new list; showing a saved list doesn't count. The per-address `newsletter` limit still counts every load.
- Required tests (spec): reader B can't read reader A's saved lists; reloading `/newsletter` the same day makes no new AI call; editing preferences makes one; signed-out `/newsletter` and `/preferences` go to sign-in.
- Page wording plain, matching the current pages. Small commits; `uv run pytest` green at each task's end.

## Review Focus

1. **Midnight in New York, not UTC:** a list made at 11 PM New York time (04:00 UTC next day) is still "today's" until New York midnight. Test in Task 2 (`test_a_list_made_late_evening_counts_for_that_new_york_day`).
2. **An event on a saved list later deleted or no longer eligible:** the saved list still shows the rest instead of crashing. Test in Task 2 (`test_saved_list_skips_events_that_no_longer_exist`).
3. **A signed-in reader with no profile** (the PR 2 "Almost there" dead end): `/newsletter` sends them to `/preferences`, and saving creates the profile. Test in Task 3 (`test_reader_without_preferences_is_sent_to_make_them`).
4. **Preferences form with nothing ticked or an empty interests box:** rejected with the form kept, nothing saved, no new list. Test in Task 3 (`test_incomplete_preferences_are_rejected_and_kept`).
5. **The AI busy when preferences just changed:** the reader sees the busy message and their previous list, and the next load tries again (the change isn't lost). Test in Task 2 (`test_ai_busy_after_a_preferences_change_tries_again_next_time`).

---

### Task 1: Saved-list table, profile editing rules, and dropping the old tables

**Files:**
- Create: `supabase/migrations/20261123000000_saved_lists.sql`
- Modify: `app/db.py` (TABLES; `save_issue`, `latest_issue`, `events_by_ids`, `update_profile`; `get_profile` returns `changed_at`), `app/models.py` (`Subscriber.changed_at`, `SavedIssue`)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces:
  - `Subscriber(id, name, interests, look_prompt, days, changed_at: datetime | None = None)`: `changed_at` is `profiles.preferences_changed_at`.
  - `@dataclass SavedIssue(made_on: date, made_at: datetime, picks: list[tuple[int, str]])`: `(event_id, reason)` in the order the AI gave them.
  - `db.save_issue(conn, user_id: str, picks: list[Pick], now: datetime) -> None`: one `as_reader` transaction: inserts the issue (`made_on` = `now` in New York as a date, `made_at` = `now`, `picks` as JSON `[{"event_id": …, "reason": …}]`) and the picks' event IDs into `sent_events` (`ON CONFLICT DO NOTHING`).
  - `db.latest_issue(conn, user_id: str) -> SavedIssue | None`: newest by `made_at`, through `as_reader`.
  - `db.events_by_ids(conn, ids: list[int]) -> dict[int, ScoredEvent]`: owner role, any eligibility.
  - `db.update_profile(conn, user_id: str, name: str, interests: str, look_prompt: str, days: list[str], now: datetime) -> bool`: through `as_reader`; sets `preferences_changed_at = now`; returns whether a row was updated.
  - `db.TABLES`: `events, dropped_events, profiles, sent_events, newsletter_issues, pastes, ai_requests, waiting_pastes`.

- [ ] **Step 1: Write the failing tests** in `tests/test_db.py` (helpers `make_user` from PR 2):

```python
def test_saved_issue_round_trip_and_marks_events_sent(conn): ...
    # save_issue with picks for events 7 and 9 (reasons "a", "b"), now = 2026-11-23 20:00 NY
    # latest_issue: made_on == date(2026, 11, 23), picks == [(7, "a"), (9, "b")]; sent_event_ids == {7, 9}
def test_latest_issue_is_the_newest(conn): ...
def test_reader_cannot_read_or_add_another_readers_lists(conn): ...
    # B saves a list; as_reader(A): select from newsletter_issues returns nothing;
    # inserting a row with user_id = B raises InsufficientPrivilege
def test_reader_can_update_only_their_own_preferences(conn): ...
    # update_profile(A) returns True and sets changed_at; as_reader(A) "update profiles set name='x' where user_id=B" changes 0 rows
def test_reader_cannot_change_first_look_columns(conn): ...
    # as_reader(A): update profiles set first_look_token = 'x' -> InsufficientPrivilege
def test_events_by_ids_includes_ineligible_and_skips_missing(conn): ...
def test_old_reader_tables_are_gone(conn): ...  # to_regclass('old_subscribers') is None
```

Update `test_every_table_has_row_level_security` only if it lists tables by name (it compares with `db.TABLES`).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_db.py -q`
Expected: FAIL (`save_issue` not defined, `newsletter_issues` does not exist)

- [ ] **Step 3: Write the migration** `supabase/migrations/20261123000000_saved_lists.sql`:
  - `drop table old_sent_events; drop table old_subscribers;`
  - `alter table profiles add column preferences_changed_at timestamptz;`
  - `newsletter_issues (id bigint generated by default as identity primary key, user_id uuid not null references profiles (user_id) on delete cascade, made_on date not null, made_at timestamptz not null, picks jsonb not null)`, index on `(user_id, made_at desc)`.
  - RLS on; `revoke all on newsletter_issues from anon, authenticated; grant select, insert on newsletter_issues to authenticated;` policies "Readers see their own lists" (select, `using (user_id = (select auth.uid()))`) and "Readers add only their own lists" (insert, `with check` the same).
  - `grant update (name, interests, look_prompt, days, preferences_changed_at) on profiles to authenticated;` policy "Readers edit their own profile" (update, `using` and `with check` `user_id = (select auth.uid())`).
  - The identity sequence needs `grant usage on sequence newsletter_issues_id_seq to authenticated` so a reader-scoped insert can take an ID.

- [ ] **Step 4: Implement the `app/db.py` and `app/models.py` changes** per the Interfaces block.

- [ ] **Step 5: Run the database tests**

Run: `uv run pytest tests/test_db.py -q`
Expected: PASS. (`tests/test_main.py` / `test_accounts.py` may fail on removed `old_*` names in fixtures until Task 3; fix any that reference them here.)

- [ ] **Step 6: Commit** `git commit -m "Add saved lists and let readers edit their own preferences"`

---

### Task 2: Show today's saved list, make a new one only when needed

**Files:**
- Create: `app/saved_lists.py`
- Modify: `app/main.py` (`GET /newsletter`; remove `POST /newsletter/sent`), `app/templates/view.html` (no "Copy this list" / "Mark as sent"; "Edit my preferences" link; the list's date), `tests/test_main.py`, `tests/test_accounts.py`, `tests/test_auth.py` (drop `/newsletter/sent`)
- Test: `tests/test_saved_lists.py`

**Interfaces:**
- Consumes: Task 1 `save_issue`, `latest_issue`, `events_by_ids`, `Subscriber.changed_at`, `SavedIssue`; `newsletter.choose_picks`, `newsletter.render_newsletter`.
- Produces, in `app/saved_lists.py`:
  - `needs_new_list(latest: SavedIssue | None, reader: Subscriber, now: datetime) -> bool`: True when `latest` is None, `latest.made_on` isn't today in New York, or `reader.changed_at` is after `latest.made_at`.
  - `saved_picks(conn, latest: SavedIssue) -> list[Pick]`: the saved picks whose events still exist, in saved order.
  - `make_list(conn, ai, reader: Subscriber, now: datetime) -> list[Pick]`: `choose_picks` over `eligible_events`, excluding `sent_event_ids`, then `save_issue`. Raises `AIUnavailable` unchanged (nothing saved).

- [ ] **Step 1: Write the failing tests** in `tests/test_saved_lists.py` (route-level, using `client_for` / `sign_up_ana` / `signed_in_ana` from `tests/test_main.py` and a counting `FakeAI`):

```python
def test_reloading_the_same_day_makes_no_new_ai_call(...): ...      # 2 loads -> ai.calls == 1, same picks shown
def test_a_new_new_york_day_makes_a_new_list(...): ...               # clock + 1 day -> ai.calls == 2
def test_a_list_made_late_evening_counts_for_that_new_york_day(...): ...
    # made 23:30 NY (04:30 UTC next day); reloaded 23:50 NY -> no new call; 00:10 NY next day -> new call
def test_new_list_skips_events_on_earlier_lists(...): ...            # day-2 AI listing excludes day-1 picks (FakeAI.prompts)
def test_saved_list_skips_events_that_no_longer_exist(...): ...      # delete one picked event row; reload shows the other
def test_ai_busy_shows_the_previous_list_with_the_message(...): ... # day 2 AI error -> AI_BUSY text and day-1 event names
def test_ai_busy_with_no_list_yet_shows_the_message(...): ...
def test_ai_busy_after_a_preferences_change_tries_again_next_time(...): ...
def test_signing_in_shows_the_saved_list_without_the_ai(...): ...    # list made at sign-up; sign in -> ai.calls unchanged
def test_list_page_has_no_copy_or_mark_as_sent(...): ...             # no 'id="copy"', no '/newsletter/sent'; has 'href="/preferences"'
def test_only_new_lists_count_toward_the_per_reader_limit(...): ... # 15 same-day loads all 200
```

Delete `test_mark_sent_excludes_next_time`, `test_mark_sent_uses_the_signed_in_reader`, `test_mark_sent_signed_out_redirects_to_sign_in`, and the `/newsletter/sent` row in `test_every_form_refuses_other_sites`; `test_list_loads_are_limited_per_reader_too` becomes a test that new-list loads past 10 an hour get 429 (advance preferences each time).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_saved_lists.py -q`
Expected: FAIL (module `app.saved_lists` not found)

- [ ] **Step 3: Implement `app/saved_lists.py` and the `/newsletter` route.** Route order after resolving the reader (unchanged from PR 2 up to the profile): `latest = db.latest_issue(...)`; if `needs_new_list`: per-account limit (429 when over), then `make_list`; on `AIUnavailable` show `AI_BUSY` above `saved_picks(latest)` (or just the message when there's none); else render `saved_picks(latest)`. The page's date line is the list's `made_on`.

- [ ] **Step 4: Run the suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit** `git commit -m "Save each reader's list and show it again instead of asking the AI each time"`

---

### Task 3: The edit-preferences page and the "Making your list" screen

**Files:**
- Create: `app/templates/preferences.html`, `app/templates/partials/making.html` (the overlay markup, style and submit script, moved out of `index.html` so other pages reuse it)
- Modify: `app/main.py` (`GET|POST /preferences`; the no-profile case goes to `/preferences`), `app/templates/index.html` (use the partial), `app/templates/view.html`, `app/templates/signin.html`, `confirm.html`, `reset.html` (show the screen on submit)
- Test: `tests/test_preferences.py`

**Interfaces:**
- Consumes: Task 1 `db.update_profile`, `db.create_profile`, `db.get_profile`; `main.DAYS`.
- Produces: routes `GET /preferences`, `POST /preferences`.

- [ ] **Step 1: Write the failing tests** in `tests/test_preferences.py`:

```python
def test_preferences_form_is_prefilled(...): ...         # name, interests, look_prompt values; ticked days checked
def test_saving_preferences_makes_a_new_list(...): ...    # POST -> 303 /newsletter; next GET -> ai.calls + 1, new interests in the AI prompt
def test_incomplete_preferences_are_rejected_and_kept(...): ...  # no days / blank interests -> 400, typed values kept, profile unchanged
def test_signed_out_and_first_look_readers_go_to_sign_in(...): ... # GET and POST /preferences -> 303 /signin
def test_reader_without_preferences_is_sent_to_make_them(...): ...
    # delete Ana's profile row; GET /newsletter -> 303 /preferences; POST valid form -> profile exists; GET /newsletter -> 200
def test_preferences_form_refuses_other_sites(...): ...   # Origin evil -> 403, profile unchanged
def test_preferences_text_is_escaped(...): ...
def test_pages_that_may_wait_show_the_making_screen(...): ...
    # 'id="making"' on /, /signin, /preferences, /reset?token_hash=x&type=recovery, /confirm?token_hash=x&type=email
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_preferences.py -q`
Expected: FAIL (404 on `/preferences`)

- [ ] **Step 3: Implement.** `POST /preferences` validates like sign-up (name, interests, at least one known day), then `update_profile`, or `create_profile` when the signed-in reader has none, and redirects to `/newsletter`. The form looks like the account pages (`partials/account_page.html`): text inputs for name and extra wishes, a textarea for interests, the seven day checkboxes. The making screen shows on submit only for forms that lead to `/newsletter`.

- [ ] **Step 4: Run the suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit** `git commit -m "Let readers edit their preferences, and cover the wait for a new list"`

---

### Task 4: Docs, dev check, deploy

**Files:** `AGENTS.md` (structure: `app/saved_lists.py`; the deploy note that this migration drops `old_*`), `SECURITY.md` (remaining issues: the reload item is fixed; `newsletter_issues` in the RLS list), `docs/index.html` (build log line after deploy)

- [ ] **Step 1: Update the docs.**
- [ ] **Step 2: Dev:** `supabase db push --db-url "$DATABASE_URL"`; run the app locally against dev; sign in, load the list twice (second instant), edit preferences (new list), reload (instant). Access test through the public API: B can't read A's `newsletter_issues` or update A's profile.
- [ ] **Step 3: Production** (deploy steps in `AGENTS.md`; the migration drops tables the running code no longer reads but adds a column it does, so stop first): `pg_dump`; stop app and timer; `supabase db push` to production; pull; restart; check `/`, `/signin`, `/newsletter` 303, `/preferences` 303 signed out; repeat the access test on production for `newsletter_issues`; build log line.
