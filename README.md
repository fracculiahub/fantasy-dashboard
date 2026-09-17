# 🏈 Fantasy Football Multi-League Dashboard

Consolidates your rosters from **Sleeper** (2 leagues) and **ESPN** (1 league) into one Streamlit dashboard: master roster, player exposure across leagues, cross-league matchup conflicts, injury news, and a live scoreboard.

**Multiple people can use the same deployed app, each with their own account.** Everyone signs up with a username + password; their Sleeper username and ESPN league ID/team name are stored server-side (in Supabase) keyed to their account, so it follows them across browsers/devices rather than being tied to one browser. ESPN cookies can either be entered per-account too, or shared app-wide if everyone's in the same private league — see the ESPN setup section below.

⚠️ **Security note:** accounts are intentionally lightweight — passwords are hashed but there's no email verification, password reset, or rate limiting. Fine for a small group of trusted testers; not meant for a public-facing app with strangers signing up.

## Prerequisites

- Python 3.10+
- A GitHub account (for deploying to Streamlit Community Cloud)
- A free [Supabase](https://supabase.com) account (for the accounts/settings database)
- Accounts on Sleeper and ESPN Fantasy with active leagues

## 1. Set up the database (Supabase)

1. Go to [supabase.com](https://supabase.com), sign up free, and create a new project.
2. Once it's ready, open the **SQL Editor** (left sidebar) and run:
   ```sql
   create table user_profiles (
     username text primary key,
     password_hash text not null,
     password_salt text not null,
     sleeper_username text default '',
     espn_league_id text default '',
     espn_team_filter text default '',
     espn_s2 text default '',
     espn_swid text default ''
   );
   ```
3. The URL and key are on two different pages:
   - **URL**: left sidebar → **Integrations → Data API → Overview**. Copy the **"API URL"** field (looks like `https://your-project-ref.supabase.co/rest/v1/`) — either with or without the `/rest/v1/` part is fine, the app normalizes it either way.
   - **Secret key**: left sidebar → **Project Settings → API Keys** (defaults to the **"Publishable and secret API keys"** tab). Under **Secret keys**, click **Reveal** and copy the key starting `sb_secret_...` — not the publishable key. This is what lets the app read/write the table; it's never exposed to visitors' browsers since only the server-side Streamlit app uses it.

## 2. Set up the platforms

### Sleeper
Nothing to configure ahead of time — just have your Sleeper **username** ready. You'll type it into the sidebar.

### ESPN — extract two cookies
1. Log in to [fantasy.espn.com](https://fantasy.espn.com) in Chrome.
2. Press `F12` to open DevTools → **Application** tab → **Cookies** → `https://espn.com`.
3. Find the rows named `espn_s2` and `SWID`, and copy their **Value** column (SWID includes the curly braces, e.g. `{ABC123...}`).
4. You'll also need your ESPN **League ID** — the numeric `leagueId` in the URL when viewing your league on fantasy.espn.com. That field also accepts the full URL or a pasted `leagueId=...` fragment.

**If everyone using this app is in the same private ESPN league** (a shared home league), only *one* member's cookies are needed — ESPN's API grants read access to the whole league to any member, not just their own team. Add them as a shared secret instead of asking every tester to extract their own:

```toml
[espn]
espn_s2 = "one-member's-espn_s2-value"
swid = "{that-same-member's-SWID-value}"
```

When this is set, the sidebar's cookie fields disappear entirely and everyone just enters their own **League ID + team name** to pick out their own team. If it's *not* set (or if people are actually in different ESPN leagues), each person enters their own `espn_s2`/`SWID` in the sidebar as before, saved to their own account.

### GitHub
Create a repository named `fantasy-dashboard` (public or private).

## 3. Local development

```bash
pip install -r requirements.txt
```

Create `.streamlit/secrets.toml` (gitignored — never commit it):

```toml
[supabase]
url = "https://your-project-ref.supabase.co"
secret_key = "paste-your-sb_secret-key-here"

# Optional -- only if everyone's in the same private ESPN league (see above)
[espn]
espn_s2 = "one-member's-espn_s2-value"
swid = "{that-same-member's-SWID-value}"
```

Run it:

```bash
streamlit run app.py
```

Sign up with any username/password, then enter your Sleeper username and ESPN League ID + cookies in the sidebar, and click **💾 Save My Settings**. Log out and back in (or from a different browser) to confirm it's remembered.

## 4. Deploy to Streamlit Community Cloud

1. Push this repo to `github.com/<you>/fantasy-dashboard`.
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app** → pick your repo, branch `main`, main file `app.py`.
3. In **Advanced settings → Secrets**, paste the same secrets block(s) from above.
4. Deploy.
5. Share the app URL with your testers — each person signs up for their own account and enters their own leagues.

## Notes & known quirks

- **ESPN cookies expire** every ~365 days (or sooner if you log out elsewhere) — if ESPN stops loading, re-extract `espn_s2` and `SWID` and re-save them (in Streamlit Secrets if shared app-wide, or in the sidebar if per-account).
- **Settings only save when you click 💾 Save My Settings** — typing into the fields updates what's fetched *this session*, but won't persist to your account until you save (this avoids hitting the database on every keystroke).
- Streamlit Cloud **does not run background jobs**, so "live" scores update whenever you (or your browser tab) refresh/reload — there's no server-side polling.
- **Yahoo Fantasy is not supported.** Yahoo discontinued self-serve Fantasy Sports API access in 2026 — new apps can no longer get read access to Fantasy data without a manual application to Yahoo's Fantasy Sports team (see [sports.yahoo.com/developer/access](https://sports.yahoo.com/developer/access/)), and that process is geared toward commercial products rather than personal dashboards.

## Project structure

```
fantasy-dashboard/
├── app.py               # Main dashboard (entry point)
├── db.py                 # Supabase-backed accounts + saved settings
├── requirements.txt
├── .gitignore
├── .streamlit/
│   └── config.toml       # Theme & layout
└── README.md
```
