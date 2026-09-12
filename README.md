# 🏈 Fantasy Football Multi-League Dashboard

Consolidates your rosters from **Sleeper** (2 leagues), **ESPN** (1 league), and **Yahoo** (1 league) into one Streamlit dashboard: master roster, player exposure across leagues, cross-league matchup conflicts, injury news, and a live scoreboard.

## Prerequisites

- Python 3.10+
- A GitHub account (for deploying to Streamlit Community Cloud)
- Accounts on Sleeper, ESPN Fantasy, and Yahoo Fantasy with active leagues

## 1. One-time setup per platform

### Sleeper
Nothing to configure ahead of time — just have your Sleeper **username** ready. You'll type it into the sidebar.

### ESPN — extract two cookies
1. Log in to [fantasy.espn.com](https://fantasy.espn.com) in Chrome.
2. Press `F12` to open DevTools → **Application** tab → **Cookies** → `https://espn.com`.
3. Find the rows named `espn_s2` and `SWID`, and copy their **Value** column (SWID includes the curly braces, e.g. `{ABC123...}`).
4. Paste them into Streamlit secrets (see step 3 below).
5. You'll also need your ESPN **League ID** — it's the numeric `leagueId` in the URL when viewing your league on fantasy.espn.com.

### Yahoo — create a free Developer App
1. Go to [developer.yahoo.com/apps/create](https://developer.yahoo.com/apps/create/).
2. Fill in:
   - **Application Name**: `Fantasy Dashboard`
   - **Redirect URI(s)**: `https://your-app.streamlit.app` for the deployed app, or `http://localhost:8501` for local dev. (You can list both, one per line, and switch which one you put in secrets depending on where you're running.)
3. Under **API Permissions**, select **Fantasy Sports** → **Read**.
4. Save, then copy the **Client ID** and **Client Secret**.
5. Paste them into Streamlit secrets (see step 3 below).

Once this is done, you never touch a terminal for Yahoo auth again — click **Connect Yahoo** in the app's sidebar, authorize in your browser, and you're done. Tokens auto-refresh.

### GitHub
Create a repository named `fantasy-dashboard` (public or private — Streamlit Cloud works with either, private just requires connecting your GitHub account).

## 2. Local development

```bash
pip install -r requirements.txt
```

Create `.streamlit/secrets.toml` (this file is gitignored — never commit it):

```toml
[espn]
espn_s2 = "paste-your-espn_s2-value-here"
swid = "{paste-your-SWID-value-here}"

[yahoo]
client_id = "paste-your-yahoo-client-id"
client_secret = "paste-your-yahoo-client-secret"
redirect_uri = "http://localhost:8501"
```

Run it:

```bash
streamlit run app.py
```

In the sidebar: enter your Sleeper username, your ESPN League ID (+ optional team name filter if the league has multiple teams you're unsure how to pick between), and click **Connect Yahoo**.

## 3. Deploy to Streamlit Community Cloud

1. Push this repo to `github.com/<you>/fantasy-dashboard`.
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app** → pick your repo, branch `main`, main file `app.py`.
3. In **Advanced settings → Secrets**, paste the same TOML block as above, but set `redirect_uri` under `[yahoo]` to your deployed URL, e.g. `https://your-app.streamlit.app`.
4. Deploy. Update the Redirect URI in your Yahoo Developer App settings to match if you change your app's URL later.

## Notes & known quirks

- **Yahoo's raw API JSON** is deeply nested with numeric-string keys and varies slightly by endpoint. `yahoo_auth.py` parses it defensively; if a league doesn't show up, it likely means Yahoo changed a response shape — the fetch functions fail closed (return empty lists) rather than crashing the app.
- **ESPN cookies expire** every ~365 days (or sooner if you log out elsewhere) — if ESPN stops loading, re-extract `espn_s2` and `SWID` and update your secrets.
- **Matchup conflicts** for Yahoo require an extra API call per league to find your opponent — this is best-effort and may not populate every week.
- Streamlit Cloud **does not run background jobs**, so "live" scores update whenever you (or your browser tab) refresh/reload — there's no server-side polling.

## Project structure

```
fantasy-dashboard/
├── app.py               # Main dashboard (entry point)
├── yahoo_auth.py         # Yahoo OAuth + Fantasy API helper
├── requirements.txt
├── .gitignore
├── .streamlit/
│   └── config.toml       # Theme & layout
└── README.md
```
