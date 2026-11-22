-- Reader accounts: each reader's preferences belong to their Supabase Auth account.
--
-- The server reads a reader's rows inside a transaction that switches to the "authenticated" role
-- with that reader's user ID (app/db.py: as_reader). The policies below then limit it to that
-- reader's own rows, so a bug in our code still can't show one reader another's data.
-- On Supabase the postgres role is a member of "authenticated", so it may switch (checked on dev, 2026-11-22).

-- The old tables stay, unread by the app, until every reader has moved to an account (PR 3 drops them).
alter table subscribers rename to old_subscribers;
alter table sent_events rename to old_sent_events;

create table profiles (
    user_id uuid primary key references auth.users (id) on delete cascade,
    name text not null,
    interests text not null,
    look_prompt text not null,
    days text[] not null,
    -- Lets a brand-new reader see their first list before confirming their email.
    first_look_token text unique,
    first_look_until timestamptz
);

create table sent_events (
    user_id uuid not null references profiles (user_id) on delete cascade,
    event_id bigint not null,
    primary key (user_id, event_id)
);

alter table profiles enable row level security;
alter table sent_events enable row level security;

-- Only what the app needs: readers read their profile, and read and add their sent events.
-- The server creates profiles as the owner role. Editing preferences comes later.
-- Revoke first, in case the project grants new tables to these roles by default.
revoke all on profiles, sent_events from anon, authenticated;
grant select on profiles to authenticated;
grant select, insert on sent_events to authenticated;

create policy "Readers see their own profile" on profiles
    for select to authenticated using (user_id = (select auth.uid()));

create policy "Readers see their own sent events" on sent_events
    for select to authenticated using (user_id = (select auth.uid()));

create policy "Readers add only their own sent events" on sent_events
    for insert to authenticated with check (user_id = (select auth.uid()));
