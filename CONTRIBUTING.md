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

A build with a Supabase project gives everyone a guest account (`Quiet Nomad`; Supabase's anonymous sign-in)
when they finish or skip the walkthrough, and offers Continue with Google after their first task
(`src/mcp_vision/buddy/account.py`); without one, there are no accounts. Sign-in is never a wall. Once:

1. Google Cloud Console → **Google Auth Platform** (OAuth consent screen): app name Plip, logo, your support email,
   homepage `https://plip.dev`, privacy policy `https://plip.dev/privacy`, authorized domain `plip.dev`, the scopes
   `openid`, `email` and `profile`, then **Publish app** (in Testing, only listed test users can sign in). Plip and
   this repo can share one project and client.
2. **Credentials → Create credentials → OAuth client ID**, type **Web application**, authorized redirect URI
   `https://<project>.supabase.co/auth/v1/callback`.
3. Supabase → **Authentication → Sign In / Providers → Google**: on, with that client ID and secret. On the same
   page, turn **Email** and **Phone** off (Plip only signs in with Google, and with Email on anyone holding the
   publishable key can add made-up accounts to the user list) and turn **Allow anonymous sign-ins** on (the account
   everyone gets after the walkthrough; `POST /auth/v1/signup` with no email). Then **Authentication → Settings →
   Allow manual linking** on: Continue with Google links Google to that guest account
   (`GET /auth/v1/user/identities/authorize`), so one person is one row. With anonymous sign-ins off, Plip keeps the
   name on the Mac and retries each launch; with manual linking off, Google signs in as a new row and the guest
   one is left behind. A Google that already has a row (their last Mac) signs in to that row; its guest one is
   left behind too. Purge anonymous rows older than 30 days now and then (Supabase's own advice): Plip keeps the
   name locally and doesn't make a new row until a sign-out or reinstall. Anyone with the publishable key can
   create anonymous rows: set **Rate Limits → anonymous users** low (30 an hour is plenty) and, if it's abused, turn
   on **Bot and Abuse Protection** (CAPTCHA); Plip can't solve a CAPTCHA, so it would then keep the name locally
   until a sign-in.
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

Who uses Plip is under **Authentication → Users**, and as a plain table in **Table Editor → schema `private` →
`users`** (name, email, provider, joined, last sign-in; it exports to CSV). `provider` is `anonymous` until Google
is linked, then `google`: count the two apart (`supabase/migrations/20261009120000_anonymous_accounts.sql` has
the query), and never fold guest rows into "sign-ups" on a slide. A trigger on `auth.users` fills it
([`supabase/migrations/`](supabase/migrations/): run them in the SQL editor on a new project; a project Plip already
uses has it). `private` isn't served by the Data API and grants nothing to `anon` or `authenticated`, so the app's
key can't read it, only the dashboard can. Deleting someone in Authentication → Users deletes their row too.

Sign-in sends only what Google and Supabase need to sign someone in; keep it that way. The setup funnel
(`mcp_vision.analytics.track`, the events in `FUNNEL`) is keyed on the install id, like the ping, and carries the
account id as a property so PostHog and the user list line up: which steps were reached, never what was asked.
README.md's Analytics section says exactly that; change both or neither.
