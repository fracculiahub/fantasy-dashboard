# 🏈 Fantasy Football Multi-League Dashboard

Consolidates your rosters from **Sleeper** (2 leagues) and **ESPN** (1 league) into one Streamlit dashboard: master roster, player exposure across leagues, cross-league matchup conflicts, injury news, and a live scoreboard.

**Multiple people can use the same deployed app.** Every visitor's Sleeper username and ESPN league ID/cookies are saved only in *their own browser* (via localStorage) — there's no shared account system, so friends/family can each connect their own leagues on the same URL without seeing your data or you seeing theirs. Settings persist across reloads automatically; there's a "🗑️ Clear saved settings for this browser" button in the sidebar to wipe them.

## Prerequisites

- Python 3.10+
- A GitHub account (for deploying to Streamlit Community Cloud)
- Accounts on Sleeper and ESPN Fantasy with active leagues

## 1. One-time setup per platform

### Sleeper
Nothing to configure ahead of time — just have your Sleeper **username** ready. You'll type it into the sidebar.

### ESPN — extract two cookies
1. Log in to [fantasy.espn.com](https://fantasy.espn.com) in Chrome.
2. Press `F12` to open DevTools → **Application** tab → **Cookies** → `https://espn.com`.
3. Find the rows named `espn_s2` and `SWID`, and copy their **Value** column (SWID includes the curly braces, e.g. `{ABC123...}`).
4. Paste them directly into the **ESPN espn_s2 cookie** / **ESPN SWID cookie** fields in the app's sidebar (each person using the app enters their own — these are saved to your browser only, never a shared secret).
5. You'll also need your ESPN **League ID** — it's the numeric `leagueId` in the URL when viewing your league on fantasy.espn.com. The League ID field also accepts the full URL or a pasted `leagueId=...` fragment.

### GitHub
Create a repository named `fantasy-dashboard` (public or private — Streamlit Cloud works with either, private just requires connecting your GitHub account).

## 2. Local development

```bash
pip install -r requirements.txt
```

No `secrets.toml` is required to run locally — everything (Sleeper username, ESPN League ID + cookies) is entered directly in the sidebar and saved to your browser.

Run it:

```bash
streamlit run app.py
```

In the sidebar: enter your Sleeper username, and your ESPN League ID + cookies (+ optional team name filter if the league has multiple teams you're unsure how to pick between). These get saved to your browser automatically — no need to re-enter them next time.

## 3. Deploy to Streamlit Community Cloud

1. Push this repo to `github.com/<you>/fantasy-dashboard`.
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app** → pick your repo, branch `main`, main file `app.py`.
3. Deploy — no secrets needed.
4. Share the app URL with friends/family — each person enters their own Sleeper username and ESPN cookies in the sidebar, saved to their browser only.

## Notes & known quirks

- **ESPN cookies expire** every ~365 days (or sooner if you log out elsewhere) — if ESPN stops loading, re-extract `espn_s2` and `SWID` and re-paste them in the sidebar.
- **Saved settings live in browser localStorage**, not on any server — clearing your browser's site data for this app, or opening it in a different browser/private window, means re-entering everything once.
- Streamlit Cloud **does not run background jobs**, so "live" scores update whenever you (or your browser tab) refresh/reload — there's no server-side polling.
- **Yahoo Fantasy is not supported.** Yahoo discontinued self-serve Fantasy Sports API access in 2026 — new apps can no longer get read access to Fantasy data without a manual application to Yahoo's Fantasy Sports team (see [sports.yahoo.com/developer/access](https://sports.yahoo.com/developer/access/)), and that process is geared toward commercial products rather than personal dashboards.

## Project structure

```
fantasy-dashboard/
├── app.py               # Main dashboard (entry point)
├── requirements.txt
├── .gitignore
├── .streamlit/
│   └── config.toml       # Theme & layout
└── README.md
```
