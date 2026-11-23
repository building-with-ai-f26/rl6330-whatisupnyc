-- Event images, and what readers save for later and rate.

-- A picture for the event, from its feed or its page's link-preview image. https only.
alter table events add column image_url text;

-- Events a reader saved for later, so their next lists lean toward similar ones.
create table saved_for_later (
    user_id uuid not null references profiles (user_id) on delete cascade,
    event_id bigint not null references events (id) on delete cascade,
    saved_at timestamptz not null,
    primary key (user_id, event_id)
);

-- How a reader rated an event they went to, 1 to 5; the AI leans toward what they loved.
create table event_ratings (
    user_id uuid not null references profiles (user_id) on delete cascade,
    event_id bigint not null references events (id) on delete cascade,
    rating smallint not null check (rating between 1 and 5),
    rated_at timestamptz not null,
    primary key (user_id, event_id)
);

-- Each reader reads and changes only their own rows. The server writes them as the reader.
alter table saved_for_later enable row level security;
alter table event_ratings enable row level security;
revoke all on saved_for_later, event_ratings from anon, authenticated;
grant select, insert, update, delete on saved_for_later, event_ratings to authenticated;

create policy "Readers manage their own saved events" on saved_for_later
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
create policy "Readers manage their own ratings" on event_ratings
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
