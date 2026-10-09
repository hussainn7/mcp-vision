-- Anonymous accounts: everyone who finishes (or skips) the walkthrough gets an account before any sign-in.
--
-- The app creates it with POST /auth/v1/signup and no email (Supabase's anonymous sign-in), named like Quiet Nomad
-- in user_metadata.name. Continue with Google later links Google to that same row (GET /auth/v1/user/identities/
-- authorize): same id, now with an email. Both need two switches in the dashboard (CONTRIBUTING.md, Sign-in):
-- Authentication → Sign In / Providers → "Allow anonymous sign-ins", and Authentication → Settings →
-- "Allow manual linking".
--
-- private.users already copies every auth.users row (20261007034653_plip_users.sql). This migration teaches it to
-- say "anonymous" for a row without an email, and to name the real provider once Google is linked: Supabase keeps
-- raw_app_meta_data ->> 'provider' = 'anonymous' forever and appends 'google' to raw_app_meta_data -> 'providers'.

create or replace function private.sync_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  real_provider text;
begin
  -- the first provider that isn't "anonymous" (there's one once Google is linked), else what Supabase says
  select p into real_provider
  from jsonb_array_elements_text(coalesce(new.raw_app_meta_data -> 'providers', '[]'::jsonb)) as t(p)
  where p <> 'anonymous'
  limit 1;

  insert into private.users (id, email, name, provider, joined_at, last_sign_in_at)
  values (
    new.id,
    new.email,
    left(coalesce(new.raw_user_meta_data ->> 'full_name', new.raw_user_meta_data ->> 'name'), 200),
    case
      when coalesce(new.email, '') = '' then 'anonymous'
      else coalesce(real_provider, new.raw_app_meta_data ->> 'provider')
    end,
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
  raise warning 'private.sync_user: % (%)', sqlerrm, sqlstate;
  return new;
end;
$$;
revoke all on function private.sync_user() from public, anon, authenticated;

-- rows made before this: the same rule, once
update private.users u
set provider = case
  when coalesce(a.email, '') = '' then 'anonymous'
  else coalesce((
    select p from jsonb_array_elements_text(coalesce(a.raw_app_meta_data -> 'providers', '[]'::jsonb)) as t(p)
    where p <> 'anonymous' limit 1), a.raw_app_meta_data ->> 'provider')
end
from auth.users a
where a.id = u.id;

-- The numbers for the traction slide, straight from the dashboard's SQL editor. Anonymous rows are not users:
-- a reinstall, a wiped ~/.config or a sign-out each make one more. Count signed_in, and show anonymous as "rows".
--   select count(*) filter (where provider = 'anonymous') as anonymous_rows,
--          count(*) filter (where provider <> 'anonymous') as signed_in
--   from private.users;
