-- Plip's user list: one row per person who signed in with Google, kept by the database itself.
--
-- The auth server writes auth.users when someone signs up or signs in; a trigger copies the parts Plip promises
-- to keep (README.md, Your account: name, email, when they joined, when they last signed in) into private.users. The app
-- never writes here and has no way to: `private` isn't a Data API schema, and anon / authenticated get no
-- grants on it, so neither the publishable key nor a signed-in person's session can read or change a row.
-- Read it in the dashboard (Table Editor → schema private → users) or the SQL editor; both run as postgres.
--
-- Deleting someone's account in Authentication → Users deletes their row too (on delete cascade).

create schema if not exists private;
revoke all on schema private from public, anon, authenticated;

create table private.users (
  id uuid primary key references auth.users (id) on delete cascade,
  email text,
  name text,
  provider text,
  joined_at timestamptz not null default now(),
  last_sign_in_at timestamptz
);
comment on table private.users is
  'Everyone who signed in to Plip. Filled by private.sync_user() from auth.users; not served by the Data API.';

-- Defense in depth: RLS on with no policies, so even a grant made by mistake later shows nobody any row.
alter table private.users enable row level security;
revoke all on table private.users from public, anon, authenticated;

-- security definer: it runs as its owner (postgres), because the auth server's role has no rights on `private`.
-- search_path is empty, so every name below is schema-qualified and nothing can be shadowed.
create function private.sync_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into private.users (id, email, name, provider, joined_at, last_sign_in_at)
  values (
    new.id,
    new.email,
    -- user_metadata is editable by its owner, so it's display text only (never trusted for anything), capped
    left(coalesce(new.raw_user_meta_data ->> 'full_name', new.raw_user_meta_data ->> 'name'), 200),
    new.raw_app_meta_data ->> 'provider',
    coalesce(new.created_at, now()),
    new.last_sign_in_at
  )
  on conflict (id) do update set
    email = excluded.email,
    name = excluded.name,
    provider = excluded.provider,
    last_sign_in_at = excluded.last_sign_in_at;
  return new;
exception when others then
  -- the list must never stand between someone and signing in; auth.users stays the source of truth
  raise warning 'private.sync_user: % (%)', sqlerrm, sqlstate;
  return new;
end;
$$;
revoke all on function private.sync_user() from public, anon, authenticated;

create trigger plip_sync_user
after insert or update of email, raw_user_meta_data, raw_app_meta_data, last_sign_in_at on auth.users
for each row execute function private.sync_user();

-- anyone who signed in before this existed
insert into private.users (id, email, name, provider, joined_at, last_sign_in_at)
select id, email,
       left(coalesce(raw_user_meta_data ->> 'full_name', raw_user_meta_data ->> 'name'), 200),
       raw_app_meta_data ->> 'provider', coalesce(created_at, now()), last_sign_in_at
from auth.users
on conflict (id) do nothing;
