# Publishing the league dashboard

The published artifact is a single file, `docs/index.html`, with the computed data
baked in. It makes no network calls, needs no server, and carries no credentials.

## One-time setup

**1. Move your cookies out of the source.** They used to live in the CONFIG block of
`league_dashboard.py`. They don't any more:

```bash
cp espn_credentials.env.example espn_credentials.env
$EDITOR espn_credentials.env          # paste SWID and espn_s2
```

`espn_credentials.env` is gitignored. Environment variables (`ESPN_SWID`, `ESPN_S2`,
`ESPN_LEAGUE_ID`) still override it, so nothing about running this on a server changes.

**2. Rotate the old cookie.** The previous `espn_s2` was committed into a source file,
so treat it as burned. Log out of espn.com and back in — that invalidates the old value.
Then paste the fresh one into `espn_credentials.env`.

**3. Start the repo.**

```bash
git init
git add .gitignore                    # add this FIRST, before anything else
git add .
git status                            # read this list before committing
git commit -m "League history dashboard"
```

Check `git status` names no `.env`, no `history_cache/`, no `league_history.json`.

## The publish loop

Refresh the data (needs cookies, hits ESPN), then build:

```bash
python3 league_dashboard.py --refresh --build-static --out docs/index.html
```

Or rebuild the page from data you already have — no cookies, no network:

```bash
python3 league_dashboard.py --static-only --out docs/index.html
```

Then:

```bash
git add docs/index.html
git commit -m "Update through week N"
git push
```

## Turning on GitHub Pages

Push to GitHub, then in the repo: **Settings → Pages → Source: Deploy from a branch**,
branch `main`, folder `/docs`. The link is
`https://<your-username>.github.io/<repo-name>/` and works on any phone, no login.

Anything that serves a static file works the same way — Netlify (drag `docs/` onto the
dashboard), Cloudflare Pages, or Vercel.

**A GitHub Pages site is public.** Anyone with the link can read it. That's what you
asked for, but it does mean the league's scoring history is on the open web. If you'd
rather it not be, Netlify supports password protection, or a private repo plus
`python3 -m http.server` on your own machine keeps it local.

## What is stripped before publishing

`--build-static` runs `sanitize_payload()` first. This matters more than it sounds:
ESPN keys every franchise by its **member SWID**, so the raw payload contains all 14
league members' SWIDs, including the one that forms half of your own login cookie.

Removed from the published file:

| Removed | Why |
|---|---|
| Member SWIDs (team keys) | Half of an ESPN auth cookie; one of them is yours |
| `owner` — full legal names | The dashboard only ever shows "First L" |
| `team_name` — ESPN team names | Same; also your display convention |
| `league_id` | No reason to hand it out |

Keys become opaque ids (`m01`, `m02`, …), assigned in sorted order so they stay stable
across rebuilds and colours don't shuffle between publishes.

Then `scan_for_credentials()` checks the finished HTML for the live cookie values, the
string `espn_s2`, and anything SWID-shaped. **If it finds anything, nothing is written.**
It refuses rather than warns, because the next step is a public push.

## Verifying before you push

```bash
python3 test_dashboard.py    # includes the sanitiser and the leak scanner
python3 test_extras.py
node test_mobile.js          # layout at desktop and phone widths (needs: npm i jsdom)
```

A quick manual sweep of the built file, which should print nothing:

```bash
grep -io "espn_s2\|swid" docs/index.html
grep -oE "\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}" docs/index.html
```

## Files

| File | Committed? |
|---|---|
| `docs/index.html` | **yes** — this is the published page |
| `dashboard.html`, `league_dashboard.py`, `league_extras.py` | yes — no secrets in them now |
| `test_*.py`, `test_mobile.js` | yes |
| `espn_credentials.env` | **never** |
| `history_cache/` | no — raw pulls, contains SWIDs and real names |
| `league_history.json` | no — computed, but still keyed by SWID |
