-- Several lists on one account ("Music", "Sport"), each with its own interests, days and email time.
-- Saved events, ratings and the events already shown stay per account, so two lists never repeat an event.
create table reader_lists (
    id bigint generated always as identity primary key,
    user_id uuid not null references profiles (user_id) on delete cascade,
    title text not null,
    interests text not null,
    days text[] not null,
    send_hour smallint not null default 8 check (send_hour in (7, 8, 9, 12, 17)),
    email_on boolean not null default true,
    emailed_on date,
    -- When its preferences last changed; a daily list made before this is out of date.
    changed_at timestamptz,
    created_at timestamptz not null default now()
);
create index reader_lists_user on reader_lists (user_id, id);

-- Every reader's current preferences become their first list, with the email as they have it now.
insert into reader_lists (user_id, title, interests, days, send_hour, email_on, emailed_on, changed_at)
select user_id, 'My list', interests, days, send_hour, email_on, emailed_on, preferences_changed_at
from profiles;

-- Each day's saved list belongs to one of the reader's lists.
alter table newsletter_issues add column list_id bigint references reader_lists (id) on delete cascade;
update newsletter_issues i set list_id = l.id from reader_lists l where l.user_id = i.user_id;
alter table newsletter_issues alter column list_id set not null;
create index newsletter_issues_list_latest on newsletter_issues (list_id, made_at desc);

-- Readers read and edit their own lists; adding and deleting go through the server, which checks the limit.
alter table reader_lists enable row level security;
-- Supabase grants new tables in full to these roles; take that back, then allow only what readers need.
revoke all on reader_lists from anon, authenticated;
grant select on reader_lists to authenticated;
grant update (title, interests, days, send_hour, email_on, changed_at) on reader_lists to authenticated;
create policy "Readers see their own lists of preferences" on reader_lists
    for select to authenticated using (user_id = (select auth.uid()));
create policy "Readers edit their own lists of preferences" on reader_lists
    for update to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
