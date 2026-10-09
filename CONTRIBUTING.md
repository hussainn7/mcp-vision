# Contributing

Plip is the Python package in `src/mcp_vision/buddy/` plus a React UI in `ui/` that
builds into one file, `src/mcp_vision/buddy/web/index.html`.

## Checks

Every change passes the same checks CI runs:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
ruff check . && python -m pytest
cd ui && npm ci && npx playwright install chromium
npm run typecheck && npm run build && npm run e2e
```

The Python tests need no model, network or display: the CLI brains run as fake
subprocesses, and a full `plip` CLI run goes against a fake Claude Code. They run in a
throwaway state folder, so they never touch your real Plip. Commit the rebuilt
`web/index.html` with any UI change.

For a bug, add a small test that fails before the fix.

## On a real Mac

Changes to the macOS layer (`*_macos.py`, `hotkey.py`, `ax_locator.py`) also need the
manual checklist in [docs/BUDDY.md](docs/BUDDY.md#manual-check-on-a-mac). Write what you
checked in the pull request.

## Releases

See [docs/RELEASING.md](docs/RELEASING.md).

### Sign-in

A build with a Supabase project asks everyone to sign in with Google at the end of the welcome tour
(`src/mcp_vision/buddy/account.py`); without one, nobody is asked. Once:

1. Google Cloud Console → **Google Auth Platform** (OAuth consent screen): app name Plip, logo, your support email,
   homepage `https://plip.dev`, privacy policy `https://plip.dev/privacy`, authorized domain `plip.dev`, the scopes
   `openid`, `email` and `profile`, then **Publish app** (in Testing, only listed test users can sign in). Plip and
   this repo can share one project and client.
2. **Credentials → Create credentials → OAuth client ID**, type **Web application**, authorized redirect URI
   `https://<project>.supabase.co/auth/v1/callback`.
3. Supabase → **Authentication → Sign In / Providers → Google**: on, with that client ID and secret. On the same
   page, turn **Email** off (and leave Phone and anonymous sign-ins off): Plip only signs in with Google, and with
   Email on anyone holding the publishable key can add made-up accounts to the user list.
4. **Authentication → URL Configuration**: Site URL `http://127.0.0.1:47823/callback`, and under Redirect URLs exactly
   `http://127.0.0.1:47823/callback`, `http://127.0.0.1:47824/callback` and `http://127.0.0.1:47825/callback` (Plip
   listens on whichever is free). Nothing else, so a sign-in can only ever come back to Plip.
5. **Project Settings → API Keys**: the **publishable** key (`sb_publishable_…`, or the legacy `anon` key), never the
   secret or `service_role` key: it ships inside the app, and `scripts/build_dmg.py` refuses a secret one. Put both
   in `~/.config/mcp-vision/.env` (never commit them):

```bash
PLIP_SUPABASE_URL=https://<project>.supabase.co
PLIP_SUPABASE_KEY=sb_publishable_…
```

Check it's live: `curl -H "apikey: $PLIP_SUPABASE_KEY" $PLIP_SUPABASE_URL/auth/v1/settings` shows `"google": true`.
`build_dmg.py` bakes them into the app's `Contents/Resources/plip.env` (`--supabase-url` / `--supabase-key` also
work), and a source checkout with them set asks you to sign in too. Turn Google on before the key goes in: with the
key set and Google off, Plip asks for a sign-in that can't work.

Who signed up is under **Authentication → Users**, and as a plain table in **Table Editor → schema `private` →
`users`** (name, email, provider, joined, last sign-in; it exports to CSV). A trigger on `auth.users` fills it
([`supabase/migrations/`](supabase/migrations/): run them in the SQL editor on a new project; a project Plip already
uses has it). `private` isn't served by the Data API and grants nothing to `anon` or `authenticated`, so the app's
key can't read it, only the dashboard can. Deleting someone in Authentication → Users deletes their row too.

Sign-in sends only what Google and Supabase need to sign someone in; keep it that way, and never link the
anonymous analytics ID to an account.
