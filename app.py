"""Fantasy Football Multi-League Dashboard.

Consolidates rosters from Sleeper (2 leagues), ESPN (1 league), and Yahoo
(1 league) into one command center: master roster, player exposure,
cross-league conflicts, injury news, and a live scoreboard.
"""

from __future__ import annotations

import json
import re
import time

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from streamlit_local_storage import LocalStorage

import yahoo_auth

st.set_page_config(page_title="Fantasy Command Center", page_icon="🏈", layout="wide")

SLEEPER_BASE = "https://api.sleeper.app/v1"
PROFILE_STORAGE_KEY = "fantasy_dashboard_profile"
PROFILE_FIELDS = ("sleeper_username", "espn_league_id", "espn_team_filter", "espn_s2", "espn_swid")

STATUS_EMOJI = {
    "ACTIVE": "🟢",
    "QUESTIONABLE": "🟡",
    "DOUBTFUL": "🟠",
    "OUT": "🔴",
    "IR": "🔴",
    "PUP": "🔴",
    "SUSPENDED": "🔴",
    "NA": "⚪",
}
SEVERITY_ORDER = {"OUT": 0, "IR": 0, "PUP": 0, "SUSPENDED": 0, "DOUBTFUL": 1, "QUESTIONABLE": 2}


def parse_espn_league_id(raw: str) -> str:
    """Tolerates a bare ID, a 'leagueId=123' fragment, or a full ESPN URL."""
    raw = (raw or "").strip()
    match = re.search(r"leagueId=(\d+)", raw)
    if match:
        return match.group(1)
    digits = re.sub(r"\D", "", raw)
    return digits or raw


def get_secrets_section(section: str) -> dict:
    """st.secrets raises if no secrets.toml exists at all, not just on missing keys."""
    try:
        return dict(st.secrets.get(section, {}))
    except Exception:
        return {}


# --------------------------------------------------------------------------
# Per-browser profile persistence (localStorage)
#
# Each visitor's Sleeper username, ESPN league/cookies, and Yahoo tokens are
# saved only in their own browser's localStorage — there is no server-side
# account system. This is what lets multiple people share one deployed app
# URL without seeing each other's leagues or credentials.
# --------------------------------------------------------------------------

def hydrate_profile_from_storage(local_storage: LocalStorage) -> None:
    if st.session_state.get("_profile_hydrated"):
        return
    st.session_state["_profile_hydrated"] = True

    raw = local_storage.getItem(PROFILE_STORAGE_KEY)
    if not raw:
        return
    try:
        profile = json.loads(raw)
    except (TypeError, ValueError):
        return

    for field in PROFILE_FIELDS:
        if profile.get(field) and field not in st.session_state:
            st.session_state[field] = profile[field]
    if profile.get("yahoo_tokens") and "yahoo_tokens" not in st.session_state:
        st.session_state["yahoo_tokens"] = profile["yahoo_tokens"]


def save_profile_to_storage(local_storage: LocalStorage, cfg: dict) -> None:
    profile = {field: cfg.get(field, "") for field in PROFILE_FIELDS}
    profile["yahoo_tokens"] = st.session_state.get("yahoo_tokens")

    snapshot = json.dumps(profile, sort_keys=True)
    if st.session_state.get("_profile_last_saved") == snapshot:
        return
    local_storage.setItem(PROFILE_STORAGE_KEY, snapshot, key="save_profile")
    st.session_state["_profile_last_saved"] = snapshot


def clear_saved_profile(local_storage: LocalStorage) -> None:
    local_storage.deleteItem(PROFILE_STORAGE_KEY, key="clear_profile")
    for field in PROFILE_FIELDS:
        st.session_state.pop(field, None)
    st.session_state.pop("yahoo_tokens", None)
    st.session_state.pop("_profile_last_saved", None)


# --------------------------------------------------------------------------
# Sleeper
# --------------------------------------------------------------------------

@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def fetch_sleeper_players() -> dict:
    resp = requests.get(f"{SLEEPER_BASE}/players/nfl", timeout=30)
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=60 * 5, show_spinner=False)
def fetch_nfl_state() -> dict:
    resp = requests.get(f"{SLEEPER_BASE}/state/nfl", timeout=15)
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=60 * 10, show_spinner=False)
def fetch_sleeper_projections(season: str, week: int) -> dict:
    """Maps player_id (str) -> projected fantasy points (PPR) for the given week."""
    try:
        resp = requests.get(
            f"https://api.sleeper.app/projections/nfl/{season}/{week}",
            params={"season_type": "regular"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return {}

    projections = {}
    for entry in data or []:
        pid = entry.get("player_id")
        if pid is None:
            continue
        pts = (entry.get("stats") or {}).get("pts_ppr")
        if pts is not None:
            projections[str(pid)] = pts
    return projections


@st.cache_data(ttl=60 * 10, show_spinner=False)
def fetch_sleeper_rosters(username: str, season: str) -> list[dict]:
    user_resp = requests.get(f"{SLEEPER_BASE}/user/{username}", timeout=15)
    user_resp.raise_for_status()
    user = user_resp.json()
    if not user:
        return []
    user_id = user["user_id"]

    leagues_resp = requests.get(f"{SLEEPER_BASE}/user/{user_id}/leagues/nfl/{season}", timeout=15)
    leagues_resp.raise_for_status()
    leagues = leagues_resp.json()

    players_db = fetch_sleeper_players()
    week = fetch_nfl_state().get("week", 1)
    projections = fetch_sleeper_projections(season, week)

    results = []
    for league in leagues:
        league_id = league["league_id"]
        league_name = league.get("name", "Sleeper League")

        rosters_resp = requests.get(f"{SLEEPER_BASE}/league/{league_id}/rosters", timeout=15)
        rosters_resp.raise_for_status()
        rosters = rosters_resp.json()

        my_roster = next((r for r in rosters if r.get("owner_id") == user_id), None)
        if not my_roster:
            continue

        matchups = []
        try:
            m_resp = requests.get(f"{SLEEPER_BASE}/league/{league_id}/matchups/{week}", timeout=15)
            m_resp.raise_for_status()
            matchups = m_resp.json()
        except requests.RequestException:
            matchups = []

        my_matchup = next((m for m in matchups if m.get("roster_id") == my_roster["roster_id"]), None)
        opp_matchup = None
        if my_matchup:
            opp_matchup = next(
                (
                    m for m in matchups
                    if m.get("matchup_id") == my_matchup.get("matchup_id")
                    and m.get("roster_id") != my_roster["roster_id"]
                ),
                None,
            )

        def build_player_list(pids, points_map):
            out = []
            for pid in pids or []:
                meta = players_db.get(pid, {})
                name = meta.get("full_name") or f"{meta.get('first_name', '')} {meta.get('last_name', '')}".strip() or pid
                out.append({
                    "player_id": pid,
                    "name": name,
                    "position": meta.get("position") or "?",
                    "nfl_team": meta.get("team") or "FA",
                    "injury_status": (meta.get("injury_status") or "ACTIVE").upper() or "ACTIVE",
                    "points": (points_map or {}).get(pid, 0.0),
                    "projected": projections.get(str(pid)),
                })
            return out

        my_players = build_player_list(my_roster.get("players"), (my_matchup or {}).get("players_points", {}))
        opp_players = build_player_list((opp_matchup or {}).get("players"), (opp_matchup or {}).get("players_points", {}))

        results.append({
            "league_id": league_id,
            "league_name": league_name,
            "platform": "Sleeper",
            "week": week,
            "players": my_players,
            "opponent_players": opp_players,
            "my_points": (my_matchup or {}).get("points"),
            "opp_points": (opp_matchup or {}).get("points"),
        })

    return results


# --------------------------------------------------------------------------
# ESPN
# --------------------------------------------------------------------------

@st.cache_data(ttl=60 * 10, show_spinner=False)
def fetch_espn_rosters(league_id: int, year: int, espn_s2: str, swid: str, team_filter: str = ""):
    from espn_api.football import League

    league = League(league_id=int(league_id), year=int(year), espn_s2=espn_s2, swid=swid)

    team = None
    if team_filter:
        team = next((t for t in league.teams if team_filter.lower() in t.team_name.lower()), None)
    if team is None and league.teams:
        team = league.teams[0]
    if team is None:
        return None

    def normalize_players(roster):
        out = []
        for p in roster:
            out.append({
                "player_id": getattr(p, "playerId", p.name),
                "name": p.name,
                "position": getattr(p, "position", "?"),
                "nfl_team": getattr(p, "proTeam", "FA") or "FA",
                "injury_status": (getattr(p, "injuryStatus", "ACTIVE") or "ACTIVE").upper(),
                "points": getattr(p, "points", 0.0),
                "projected": getattr(p, "projected_points", 0.0),
            })
        return out

    my_players = normalize_players(team.roster)

    my_points = None
    opp_points = None
    opp_players: list[dict] = []
    week = getattr(league, "current_week", None)
    try:
        box_scores = league.box_scores(week) if week else league.box_scores()
        for bs in box_scores:
            if getattr(bs.home_team, "team_id", None) == team.team_id:
                my_points, opp_points = bs.home_score, bs.away_score
                opp_players = normalize_players(bs.away_lineup)
                break
            if getattr(bs.away_team, "team_id", None) == team.team_id:
                my_points, opp_points = bs.away_score, bs.home_score
                opp_players = normalize_players(bs.home_lineup)
                break
    except Exception:
        pass

    return {
        "league_id": league_id,
        "league_name": league.settings.name,
        "platform": "ESPN",
        "week": week,
        "players": my_players,
        "opponent_players": opp_players,
        "my_points": my_points,
        "opp_points": opp_points,
        "team_name": team.team_name,
    }


# --------------------------------------------------------------------------
# Yahoo
# --------------------------------------------------------------------------

@st.cache_data(ttl=60 * 10, show_spinner=False)
def fetch_yahoo_rosters(access_token: str, season: int) -> list[dict]:
    teams = yahoo_auth.fetch_yahoo_teams(access_token, season)
    results = []
    for team in teams:
        team_key = team["team_key"]
        league_key = team["league_key"]
        league_name = yahoo_auth.fetch_league_name(access_token, league_key)
        players = yahoo_auth.fetch_yahoo_roster(access_token, team_key)
        for p in players:
            p["points"] = 0.0

        opp_players = []
        try:
            opp_players = yahoo_auth.fetch_matchup_opponent_roster(access_token, team_key, league_key, week=0)
        except Exception:
            opp_players = []

        results.append({
            "league_id": league_key,
            "league_name": league_name,
            "platform": "Yahoo",
            "week": None,
            "players": players,
            "opponent_players": opp_players,
            "my_points": None,
            "opp_points": None,
            "team_name": team.get("name", "My Team"),
        })
    return results


# --------------------------------------------------------------------------
# Normalization / aggregation helpers
# --------------------------------------------------------------------------

def leagues_to_dataframe(all_leagues: list[dict]) -> pd.DataFrame:
    rows = []
    for lg in all_leagues:
        for p in lg["players"]:
            rows.append({
                "Player": p["name"],
                "Position": p.get("position", "?"),
                "NFL Team": p.get("nfl_team", "FA"),
                "League": lg["league_name"],
                "Platform": lg["platform"],
                "Injury Status": p.get("injury_status", "ACTIVE"),
                "Points": p.get("points", 0.0) or 0.0,
                "Projected": p.get("projected"),
            })
    if not rows:
        return pd.DataFrame(
            columns=["Player", "Position", "NFL Team", "League", "Platform", "Injury Status", "Points", "Projected"]
        )
    return pd.DataFrame(rows)


def status_badge(status: str) -> str:
    return f"{STATUS_EMOJI.get(status, '⚪')} {status}"


def compute_exposure(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    exposure = (
        df.groupby("Player")
        .agg(Leagues=("League", "nunique"), League_List=("League", lambda x: ", ".join(sorted(set(x)))))
        .reset_index()
    )
    exposure = exposure[exposure["Leagues"] >= 2].sort_values("Leagues", ascending=False)
    return exposure


def compute_conflicts(all_leagues: list[dict]) -> pd.DataFrame:
    """Players you own in one league that your opponent owns/starts in another league."""
    my_players_by_league = {lg["league_name"]: {p["name"] for p in lg["players"]} for lg in all_leagues}

    rows = []
    for lg in all_leagues:
        opp_names = {p["name"] for p in lg.get("opponent_players", [])}
        if not opp_names:
            continue
        for other_league, my_names in my_players_by_league.items():
            if other_league == lg["league_name"]:
                continue
            overlap = opp_names & my_names
            for player in overlap:
                rows.append({
                    "Player": player,
                    "Your League": other_league,
                    "Opponent's League": lg["league_name"],
                    "Opponent Team": lg.get("team_name", ""),
                    "Week": lg.get("week"),
                })
    if not rows:
        return pd.DataFrame(columns=["Player", "Your League", "Opponent's League", "Opponent Team", "Week"])
    return pd.DataFrame(rows).drop_duplicates()


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------

def render_sidebar(local_storage: LocalStorage):
    st.sidebar.title("🏈 League Connections")

    season = st.sidebar.number_input("Season", min_value=2018, max_value=2035, value=2026, step=1)

    st.sidebar.divider()
    st.sidebar.subheader("Sleeper")
    sleeper_username = st.sidebar.text_input("Sleeper username", value=st.session_state.get("sleeper_username", ""))
    st.session_state["sleeper_username"] = sleeper_username
    st.sidebar.caption("✅ Connected" if sleeper_username else "❌ Not connected")

    st.sidebar.divider()
    st.sidebar.subheader("ESPN")
    espn_league_id = st.sidebar.text_input(
        "ESPN League ID",
        value=st.session_state.get("espn_league_id", ""),
        placeholder="e.g. 720554938",
        help="Just the numeric ID — pasting the full URL or 'leagueId=...' also works.",
    )
    espn_team_filter = st.sidebar.text_input("Your team name (filter)", value=st.session_state.get("espn_team_filter", ""))
    espn_s2 = st.sidebar.text_input(
        "ESPN espn_s2 cookie", value=st.session_state.get("espn_s2", ""), type="password"
    )
    espn_swid = st.sidebar.text_input(
        "ESPN SWID cookie", value=st.session_state.get("espn_swid", ""), type="password"
    )
    st.sidebar.caption("Saved only in your browser — see README for how to extract these cookies.")
    st.session_state["espn_league_id"] = espn_league_id
    st.session_state["espn_team_filter"] = espn_team_filter
    st.session_state["espn_s2"] = espn_s2
    st.session_state["espn_swid"] = espn_swid
    espn_ready = bool(espn_league_id) and bool(espn_s2) and bool(espn_swid)
    st.sidebar.caption("✅ Connected" if espn_ready else "❌ Not connected (needs League ID + both cookies)")

    st.sidebar.divider()
    st.sidebar.subheader("Yahoo")
    yahoo_secrets = get_secrets_section("yahoo")
    yahoo_client_id = yahoo_secrets.get("client_id")
    yahoo_client_secret = yahoo_secrets.get("client_secret")
    yahoo_redirect_uri = yahoo_secrets.get("redirect_uri", "http://localhost:8501")

    if not yahoo_client_id or not yahoo_client_secret:
        st.sidebar.caption("❌ Not connected (add client_id/secret to Secrets)")
    elif st.session_state.get("yahoo_tokens"):
        st.sidebar.caption("✅ Connected")
        if st.sidebar.button("Disconnect Yahoo"):
            st.session_state.pop("yahoo_tokens", None)
            st.rerun()
    else:
        query_params = st.query_params
        code = query_params.get("code")
        if code and not st.session_state.get("yahoo_tokens"):
            try:
                tokens = yahoo_auth.exchange_code_for_tokens(code, yahoo_client_id, yahoo_client_secret, yahoo_redirect_uri)
                st.session_state["yahoo_tokens"] = tokens
                st.query_params.clear()
                st.rerun()
            except requests.RequestException as e:
                st.sidebar.error(f"Yahoo auth failed: {e}")

        if st.sidebar.button("🔗 Connect Yahoo"):
            auth_url = yahoo_auth.get_authorization_url(yahoo_client_id, yahoo_redirect_uri)
            st.sidebar.markdown(f"[Click here to authorize with Yahoo]({auth_url})")
        st.sidebar.caption("❌ Not connected")

    st.sidebar.divider()
    if st.sidebar.button("🔄 Sync All Platforms", use_container_width=True):
        fetch_sleeper_rosters.clear()
        fetch_espn_rosters.clear()
        fetch_yahoo_rosters.clear()
        fetch_nfl_state.clear()
        st.rerun()

    if st.sidebar.button("🗑️ Clear saved settings for this browser", use_container_width=True):
        clear_saved_profile(local_storage)
        st.rerun()

    return {
        "season": season,
        "sleeper_username": sleeper_username,
        "espn_league_id": espn_league_id,
        "espn_team_filter": espn_team_filter,
        "espn_s2": espn_s2,
        "espn_swid": espn_swid,
        "yahoo_client_id": yahoo_client_id,
        "yahoo_client_secret": yahoo_client_secret,
        "yahoo_redirect_uri": yahoo_redirect_uri,
    }


# --------------------------------------------------------------------------
# Data loading orchestration
# --------------------------------------------------------------------------

def load_all_leagues(cfg: dict) -> list[dict]:
    all_leagues = []

    if cfg["sleeper_username"]:
        try:
            all_leagues.extend(fetch_sleeper_rosters(cfg["sleeper_username"], str(cfg["season"])))
        except requests.RequestException as e:
            st.warning(f"Sleeper fetch failed: {e}")

    if cfg["espn_league_id"] and cfg["espn_s2"] and cfg["espn_swid"]:
        try:
            espn_data = fetch_espn_rosters(
                parse_espn_league_id(cfg["espn_league_id"]),
                cfg["season"],
                cfg["espn_s2"],
                cfg["espn_swid"],
                cfg["espn_team_filter"],
            )
            if espn_data:
                all_leagues.append(espn_data)
        except Exception as e:
            st.warning(f"ESPN fetch failed: {e}")

    if st.session_state.get("yahoo_tokens") and cfg["yahoo_client_id"] and cfg["yahoo_client_secret"]:
        try:
            tokens = yahoo_auth.ensure_valid_token(
                st.session_state["yahoo_tokens"], cfg["yahoo_client_id"], cfg["yahoo_client_secret"], cfg["yahoo_redirect_uri"]
            )
            st.session_state["yahoo_tokens"] = tokens
            all_leagues.extend(fetch_yahoo_rosters(tokens["access_token"], cfg["season"]))
        except Exception as e:
            st.warning(f"Yahoo fetch failed: {e}")

    return all_leagues


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    local_storage = LocalStorage()
    hydrate_profile_from_storage(local_storage)

    cfg = render_sidebar(local_storage)
    st.title("🏈 Fantasy Football Command Center")

    all_leagues = load_all_leagues(cfg)
    save_profile_to_storage(local_storage, cfg)
    df = leagues_to_dataframe(all_leagues)

    # KPI cards
    platforms_active = len({lg["platform"] for lg in all_leagues})
    leagues_connected = len(all_leagues)
    injury_flags = int((df["Injury Status"] != "ACTIVE").sum()) if not df.empty else 0
    top_exposure_df = compute_exposure(df)
    top_exposure = top_exposure_df.iloc[0]["Player"] if not top_exposure_df.empty else "—"

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total Rostered Players", len(df))
    k2.metric("Connected Leagues", leagues_connected, help="Target: 4")
    k3.metric("Platforms Active", platforms_active, help="Target: 3")
    k4.metric("Highest-Exposure Player", top_exposure)
    k5.metric("Players With Injury Flags", injury_flags)

    st.divider()

    if df.empty:
        st.info("Connect at least one platform in the sidebar to load your rosters.")
        return

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📋 Master Roster", "🔥 Player Exposure", "⚠️ Multi-League Conflicts", "📰 News & Injuries", "🏆 Live Scoreboard"
    ])

    with tab1:
        st.subheader("Master Roster")
        col1, col2, col3, col4 = st.columns(4)
        pos_filter = col1.multiselect("Position", sorted(df["Position"].unique()))
        league_filter = col2.multiselect("League", sorted(df["League"].unique()))
        platform_filter = col3.multiselect("Platform", sorted(df["Platform"].unique()))
        name_search = col4.text_input("Search name")

        filtered = df.copy()
        if pos_filter:
            filtered = filtered[filtered["Position"].isin(pos_filter)]
        if league_filter:
            filtered = filtered[filtered["League"].isin(league_filter)]
        if platform_filter:
            filtered = filtered[filtered["Platform"].isin(platform_filter)]
        if name_search:
            filtered = filtered[filtered["Player"].str.contains(name_search, case=False, na=False)]

        display_df = filtered.copy()
        display_df["Injury Status"] = display_df["Injury Status"].apply(status_badge)
        display_df["Projected"] = display_df["Projected"].apply(lambda v: f"{v:.1f}" if pd.notna(v) else "—")
        st.dataframe(display_df, use_container_width=True, hide_index=True)

    with tab2:
        st.subheader("Player Exposure — owned in 2+ leagues")
        exposure = compute_exposure(df)
        if exposure.empty:
            st.info("No overlapping players across your connected leagues yet.")
        else:
            st.dataframe(exposure, use_container_width=True, hide_index=True)
            fig = px.bar(exposure, x="Player", y="Leagues", title="Portfolio concentration", text="League_List")
            fig.update_layout(xaxis_title="", yaxis_title="Leagues rostered in")
            st.plotly_chart(fig, use_container_width=True)

    with tab3:
        st.subheader("Overlap Alerts")
        exposure = compute_exposure(df)
        if exposure.empty:
            st.info("No cross-league ownership overlaps detected.")
        else:
            st.dataframe(
                exposure.rename(columns={"Leagues": "League Count", "League_List": "Leagues"}),
                use_container_width=True,
                hide_index=True,
            )

        st.subheader("Matchup Conflicts")
        st.caption("A player you own in one league who is rostered by your opponent in another league this week.")
        conflicts = compute_conflicts(all_leagues)
        if conflicts.empty:
            st.info("No matchup conflicts detected this week.")
        else:
            for week, group in conflicts.groupby("Week"):
                st.markdown(f"**Week {week}**" if pd.notna(week) else "**This week**")
                st.dataframe(group.drop(columns=["Week"]), use_container_width=True, hide_index=True)

    with tab4:
        st.subheader("News & Injuries")
        flagged = df[df["Injury Status"] != "ACTIVE"].copy()
        if flagged.empty:
            st.success("No injury flags on your rostered players. 🎉")
        else:
            flagged["_severity"] = flagged["Injury Status"].map(SEVERITY_ORDER).fillna(3)
            flagged = flagged.sort_values("_severity").drop(columns=["_severity"])
            flagged["Injury Status"] = flagged["Injury Status"].apply(status_badge)
            st.dataframe(
                flagged[["Player", "Injury Status", "NFL Team", "League", "Platform"]],
                use_container_width=True,
                hide_index=True,
            )

    with tab5:
        st.subheader("Live Matchup Scoreboard")
        st.caption("Auto-refresh: use the Sync button — Streamlit Cloud does not auto-poll in the background.")
        for lg in all_leagues:
            my_pts = lg.get("my_points")
            opp_pts = lg.get("opp_points")
            with st.container(border=True):
                c1, c2, c3 = st.columns([2, 1, 1])
                c1.markdown(f"**{lg['league_name']}** · {lg['platform']}" + (f" · Week {lg['week']}" if lg.get("week") else ""))
                c2.metric("Your Score", f"{my_pts:.1f}" if isinstance(my_pts, (int, float)) else "—")
                c3.metric("Opponent Score", f"{opp_pts:.1f}" if isinstance(opp_pts, (int, float)) else "—")
                if lg["players"]:
                    roster_df = pd.DataFrame([
                        {
                            "Player": p["name"],
                            "Position": p.get("position", "?"),
                            "Points": p.get("points", 0.0),
                            "Projected": p.get("projected") if p.get("projected") is not None else "—",
                        }
                        for p in lg["players"]
                    ])
                    st.dataframe(roster_df, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
