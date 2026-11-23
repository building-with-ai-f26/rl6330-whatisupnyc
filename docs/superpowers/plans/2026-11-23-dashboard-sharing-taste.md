# Dashboard, Sharing, Saved-for-Later, Ratings and Images Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal (Ryan, 2026-11-23):** readers can share an event with friends; save events for later so the next lists recommend similar ones; use a dashboard of the events from their lists, select some and get another list picked from that; rate events they went to so the AI picks by those ratings. Lists get images. There are two versions of the list: a full-screen dashboard on the website, and a compact email newsletter.

**Architecture:**
- **Images:** an `image_url` on each event, taken from the feeds that carry one (Ticketmaster, The Events Calendar WordPress sites, NPS), otherwise from the `og:image` of the event page the collector already fetches.
- **Taste:** two reader-owned tables, `saved_for_later` and `event_ratings`, under the same Row Level Security pattern as `sent_events`. Their contents become a short "taste" section in the AI prompt, alongside any events the reader selected with "More like these".
- **The two list versions:**
  - `/newsletter` becomes a dashboard (`dashboard.html`): full-width cards with images, Save for later, Share, and selection for More like these, plus "Saved for later" and "Rate what you went to" sections.
  - The email version is the existing email-safe `newsletter.html`, made compact with small thumbnails. It's previewed at `/newsletter/email`, and sent to the reader's own inbox through Resend when `RESEND_API_KEY` is set on the server.
- **Sharing:** a public `/e/{event_id}` page with link-preview tags, shared with the browser's share sheet or a copied link.

**Spec:** this plan (the goal above). Builds on `docs/superpowers/specs/2026-11-21-supabase-accounts-design.md` and the PR 3 plan `docs/superpowers/plans/2026-11-23-saved-lists-and-preferences.md`.

## Global Constraints
- AGENTS.md rules hold: readers never see scores or ratings of sources; code confirms every AI pick came from the checked list; no invented events.
- Images: only `https://` URLs are stored or shown (no mixed content, no `javascript:`/`data:`). An event with no image shows a plain block, never a broken image.
- Reader data (saved, ratings) is readable and writable only by its owner: RLS with `user_id = auth.uid()`, written through `db.as_reader`; `event_id` references `events` so no made-up IDs.
- A reader can save, rate or select for "More like these" only events that were on one of their own lists.
- Ratings: whole numbers 1–5; only for events that have already started.
- "More like these" makes a new list (one AI call) and counts toward the per-reader new-list limit (10 an hour).
- The email version stays compact and email-safe (tables, inline styles, thumbnails at most 96 px). Emails go only to the signed-in reader's own address, at most 3 a day per reader, and only when `RESEND_API_KEY` is set.
- `/e/{id}` is public and shows only what a list shows (name, date, venue, image, link); no reader data.

## Review Focus
1. **An image URL that is `http:`, relative, or junk:** it's made absolute and https-only, or dropped (Task 1 `test_page_image_*`).
2. **A reader posting another reader's or a never-listed event ID** to save, rate or more-like-these: refused with 404 and nothing stored (Task 2/4 tests).
3. **Rating an event that hasn't happened yet, or a rating outside 1–5:** refused with 400 (Task 2).
4. **A prompt with taste lines:** event names in the taste section come only from the database, and an empty taste adds nothing (Task 3).
5. **The email preview/send for a reader with no list yet or no key configured:** a plain message, not an error (Task 6).

---

### Task 1: Event images
- Migration `20261124000000_dashboard.sql` (shared with Task 2): `alter table events add column image_url text;`
- `CandidateEvent.image_url: str | None = None`; `db.save_event` stores it (on conflict, `coalesce(excluded.image_url, events.image_url)`); `_row_to_scored` reads it.
- `app/pages.py: page_image(html: str, page_url: str) -> str | None`: `og:image`, then `twitter:image`, made absolute, kept only if https.
- Sources: Ticketmaster `images` (prefer ratio 16_9 with width ≥ 640, else the widest), WP events `image.url`, NPS `images[0].url` (made absolute on nps.gov). Each goes through one `clean_image_url(url, base=None)` in `app/pages.py`.
- Pipeline: a checked event with no feed image takes `page_image` from its own fetched page. An already-saved event seen again with a feed image and none stored gets it: `db.fill_event_image(conn, dedupe_key, url)`.
- Tests: `tests/test_images.py`. Covers the og:image, twitter:image, relative, http, junk and missing cases; each source's mapping using a record with an image; save and fill-in; the pipeline taking a page image.

### Task 2: Saved for later and ratings (data)
- Migration adds:
  - `saved_for_later (user_id uuid references profiles on delete cascade, event_id bigint references events on delete cascade, saved_at timestamptz not null, primary key (user_id, event_id))`;
  - `event_ratings (user_id …, event_id …, rating smallint not null check (rating between 1 and 5), rated_at timestamptz not null, primary key (user_id, event_id))`.
- RLS on both. Grant select, insert, update, delete to `authenticated`, with policies `using`/`with check` `user_id = (select auth.uid())`.
- `db` (all through `as_reader`):
  - `history_event_ids(conn, user_id) -> set[int]` (every event on the reader's saved lists);
  - `save_for_later(conn, user_id, event_id, now)` and `unsave(conn, user_id, event_id)`;
  - `saved_for_later_ids(conn, user_id) -> list[int]` (newest first);
  - `rate_event(conn, user_id, event_id, rating, now)` (upsert);
  - `ratings(conn, user_id) -> dict[int, int]`.
- Tests in `tests/test_db.py`: round trips; reader B can't read or write A's rows; a bad rating is refused by the check; an unknown event is refused by the foreign key.

### Task 3: Taste in the AI prompt, and More like these
- `app/newsletter.py: Taste(saved: list[str], liked: list[str], disliked: list[str], more_like: list[str])` holds event names. `choose_picks(..., taste: Taste | None = None)` adds lines only for non-empty parts:
  - "Reader saved these for later (suggest similar):"
  - "Reader loved (4–5 stars):"
  - "Reader didn't enjoy (1–2 stars; avoid similar):"
  - "Reader asked for more like:"

  Names are joined with `; ` and capped at 10 per line.
- `app/saved_lists.py: taste_for(conn, reader) -> Taste`. `make_list(conn, ai, reader, now, more_like_ids: list[int] | None = None)` passes the taste.
- Route `POST /newsletter/more` (event_ids from the reader's history only, at least 1, at most 10). It runs `make_list` with `more_like_ids`, counts toward the per-reader limit, and redirects to `/newsletter`.
- Tests: prompt contents for each part and for an empty taste; More like these with foreign IDs is refused (404); the new list exists after it.

### Task 4: The dashboard
- `GET /newsletter` renders `dashboard.html` (full width, responsive grid):
  - **Today's picks:** cards with the image, name, when, venue, the AI's reason, "Go to event" link, a Save for later/Saved toggle, and Share. Each card has a checkbox inside the "More like these" form.
  - **Saved for later:** upcoming saved events.
  - **Rate what you went to:** started events from the reader's history, each with 1–5 buttons and its current rating.
  - Links to Edit my preferences and Email version.

  Busy and banner messages behave as before.
- Routes: `POST /events/{id}/save`, `POST /events/{id}/unsave`, `POST /events/{id}/rate` (form `rating`). Each requires sign-in or a first look, accepts only events in the reader's history (404 otherwise), and redirects to `/newsletter#event-{id}`.
- Share button: `navigator.share({title, url})`, falling back to copying the link and showing "Link copied". Share URL `https://<host>/e/{id}`.
- Tests: `tests/test_dashboard.py`.

### Task 5: Public share page
- `GET /e/{event_id}` (`event.html`): name, date, venue, image, "Go to event" link and "Get your own list" link to `/`, with `og:title`, `og:description` and `og:image` tags. Unknown ID → 404 page. No session needed, and nothing about who shared it.
- Tests: content, og tags, 404, no reader data, escaping.

### Task 6: The email version
- `newsletter.html` gains a thumbnail cell (`width="72"`, rounded) for events with an image; otherwise unchanged and compact.
- `GET /newsletter/email`: the reader's current list rendered with `render_newsletter`, inside a thin page with "Send it to my email" (only when sending is configured) and a link back.
- `POST /newsletter/email`: sends the email to the signed-in reader's own address via `app/emailer.py: send_list(http, api_key, to, html, subject)`. It posts to the Resend API from `whatisupnyc <list@whatisupnyc.com>`. Limit: `email_account` (3, 86400) per reader. First-look readers must sign in.
- `AppDeps.resend_key: str = ""` comes from `RESEND_API_KEY`. Empty means previews only.
- Tests: thumbnail rendering and size; send called with the reader's own email; the limit; no key means no button and a 404 on POST.

### Task 7: Docs, review, dev, production
- `AGENTS.md` (structure, the new tables), `SECURITY.md` (new tables and routes; the public share page), `.env.example`. Fresh whole-branch review, dev push and checks, production deploy with backups (stop, migrate, pull, start, check), a public-API access test for the new tables, and the build log line.
