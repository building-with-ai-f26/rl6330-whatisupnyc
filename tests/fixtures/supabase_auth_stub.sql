-- A tiny stand-in for the parts of Supabase that the migrations use, so tests run on a local Postgres.
-- On Supabase these already exist: the auth schema, auth.users, auth.uid(), and the anon/authenticated roles.
drop schema if exists auth cascade;
create schema auth;

create table auth.users (
    id uuid primary key default gen_random_uuid(),
    email text,
    email_confirmed_at timestamptz
);

-- Supabase's auth.uid(): the signed-in user's ID, from the request's verified login claims.
create function auth.uid() returns uuid
language sql stable
as $$ select nullif(current_setting('request.jwt.claims', true)::json ->> 'sub', '')::uuid $$;

-- Roles are shared by every database in the local cluster, so create them only once.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
end
$$;

-- Supabase grants these by default.
grant usage on schema auth to anon, authenticated;
grant execute on function auth.uid() to anon, authenticated;
grant usage on schema public to anon, authenticated;
