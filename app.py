"""Fantasy Football Multi-League Dashboard.

Consolidates rosters from Sleeper (2 leagues) and ESPN (1 league) into one
command center: master roster, player exposure, cross-league conflicts,
injury news, and a live scoreboard. Each person who uses this deployed app
has their own account (username + password, stored in Supabase) so their
league settings follow them across browsers/devices.
"""

from __future__ import annotations

import re

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

import db

st.set_page_config(page_title="Fantasy Command Center", page_icon="🏈", layout="wide")

SLEEPER_BASE = "https://api.sleeper.app/v1"
PROFILE_FIELDS = db.PROFILE_FIELDS

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


def get_shared_espn_cookies() -> tuple[str | None, str | None]:
    """App-wide ESPN cookies (Secrets), used when everyone's in the same private
    league: one member's cookies unlock read access to the whole league via
    the API, so nobody else needs to extract their own. Falls back to
    per-user cookies (entered in the sidebar) if this isn't configured."""
    secrets = get_secrets_section("espn")
    return secrets.get("espn_s2"), secrets.get("swid")


# --------------------------------------------------------------------------
# Accounts (Supabase-backed)
#
# Each person gets their own username/password account. League settings are
# stored server-side in Supabase, keyed by username, so they follow a user
# across browsers/devices rather than being tied to one browser's storage.
# --------------------------------------------------------------------------

def get_supabase_config() -> tuple[str, str] | None:
    secrets = get_secrets_section("supabase")
    url, key = secrets.get("url"), secrets.get("secret_key")
    if not url or not key:
        return None
    return url, key


def load_user_profile_into_session(user_row: dict) -> None:
    for field in PROFILE_FIELDS:
        st.session_state[field] = user_row.get(field) or ""


def render_login(supabase_cfg: tuple[str, str]) -> None:
    supabase_url, secret_key = supabase_cfg
    st.title("🏈 Fantasy Football Command Center")
    st.caption("Sign in or create an account to save your leagues.")

    login_tab, signup_tab = st.tabs(["Log in", "Sign up"])

    with login_tab:
        with st.form("login_form"):
            username = st.text_input("Username", key="login_username")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Log in")
        if submitted:
            try:
                user = db.authenticate(supabase_url, secret_key, username, password)
            except requests.RequestException as e:
                st.error(f"Could not reach the database: {e}")
            else:
                if user:
                    st.session_state["username"] = user["username"]
                    load_user_profile_into_session(user)
                    st.rerun()
                else:
                    st.error("Incorrect username or password.")

    with signup_tab:
        with st.form("signup_form"):
            new_username = st.text_input("Choose a username", key="signup_username")
            new_password = st.text_input("Choose a password", type="password", key="signup_password")
            signup_submitted = st.form_submit_button("Create account")
        if signup_submitted:
            if not new_username.strip() or not new_password:
                st.error("Username and password can't be empty.")
            else:
                try:
                    user = db.create_user(supabase_url, secret_key, new_username, new_password)
                except db.UsernameTakenError as e:
                    st.error(str(e))
                except requests.RequestException as e:
                    st.error(f"Could not reach the database: {e}")
                else:
                    st.session_state["username"] = user["username"]
                    load_user_profile_into_session(user)
                    st.rerun()


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

        users_by_id = {}
        try:
            u_resp = requests.get(f"{SLEEPER_BASE}/league/{league_id}/users", timeout=15)
            u_resp.raise_for_status()
            users_by_id = {u["user_id"]: (u.get("display_name") or u.get("username") or "Opponent") for u in u_resp.json()}
        except requests.RequestException:
            users_by_id = {}

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

        opp_roster = None
        if opp_matchup:
            opp_roster = next((r for r in rosters if r.get("roster_id") == opp_matchup.get("roster_id")), None)
        opp_team_name = users_by_id.get((opp_roster or {}).get("owner_id"), "Opponent")

        def build_player_list(pids, points_map, starters):
            starter_rank = {pid: i for i, pid in enumerate(starters or [])}
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
                    "starter": pid in starter_rank,
                    "_lineup_order": starter_rank.get(pid, 999),
                })
            out.sort(key=lambda p: p["_lineup_order"])
            for p in out:
                p.pop("_lineup_order")
            return out

        my_players = build_player_list(
            my_roster.get("players"), (my_matchup or {}).get("players_points", {}), (my_matchup or {}).get("starters")
        )
        opp_players = build_player_list(
            (opp_matchup or {}).get("players"), (opp_matchup or {}).get("players_points", {}), (opp_matchup or {}).get("starters")
        )

        results.append({
            "league_id": league_id,
            "league_name": league_name,
            "platform": "Sleeper",
            "week": week,
            "players": my_players,
            "opponent_players": opp_players,
            "my_points": (my_matchup or {}).get("points"),
            "opp_points": (opp_matchup or {}).get("points"),
            "opp_team_name": opp_team_name,
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
            slot = getattr(p, "slot_position", None) or getattr(p, "lineupSlot", "BE")
            out.append({
                "player_id": getattr(p, "playerId", p.name),
                "name": p.name,
                "position": getattr(p, "position", "?"),
                "nfl_team": getattr(p, "proTeam", "FA") or "FA",
                "injury_status": (getattr(p, "injuryStatus", "ACTIVE") or "ACTIVE").upper(),
                "points": getattr(p, "points", 0.0),
                "projected": getattr(p, "projected_points", 0.0),
                "starter": slot not in ("BE", "IR"),
            })
        out.sort(key=lambda p: 0 if p["starter"] else 1)
        return out

    my_players = normalize_players(team.roster)

    my_points = None
    opp_points = None
    opp_players: list[dict] = []
    opp_team_name = "Opponent"
    week = getattr(league, "current_week", None)
    try:
        box_scores = league.box_scores(week) if week else league.box_scores()
        for bs in box_scores:
            if getattr(bs.home_team, "team_id", None) == team.team_id:
                my_points, opp_points = bs.home_score, bs.away_score
                opp_players = normalize_players(bs.away_lineup)
                opp_team_name = getattr(bs.away_team, "team_name", "Opponent")
                break
            if getattr(bs.away_team, "team_id", None) == team.team_id:
                my_points, opp_points = bs.away_score, bs.home_score
                opp_players = normalize_players(bs.home_lineup)
                opp_team_name = getattr(bs.home_team, "team_name", "Opponent")
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
        "opp_team_name": opp_team_name,
    }


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


def roster_table_df(players: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Player": p["name"],
            "Position": p.get("position", "?"),
            "Points": p.get("points", 0.0) or 0.0,
            "Projected": f"{p['projected']:.1f}" if p.get("projected") is not None else "—",
        }
        for p in players
    ])


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

def render_sidebar(supabase_cfg: tuple[str, str]):
    st.sidebar.title("🏈 League Connections")
    st.sidebar.caption(f"Logged in as **{st.session_state['username']}**")
    if st.sidebar.button("Log out", use_container_width=True):
        for field in PROFILE_FIELDS:
            st.session_state.pop(field, None)
        st.session_state.pop("username", None)
        st.rerun()

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

    shared_espn_s2, shared_espn_swid = get_shared_espn_cookies()
    if shared_espn_s2 and shared_espn_swid:
        espn_s2, espn_swid = shared_espn_s2, shared_espn_swid
        st.sidebar.caption("Using shared ESPN access (configured by the app owner) — just enter your team name above.")
    else:
        espn_s2 = st.sidebar.text_input(
            "ESPN espn_s2 cookie", value=st.session_state.get("espn_s2", ""), type="password"
        )
        espn_swid = st.sidebar.text_input(
            "ESPN SWID cookie", value=st.session_state.get("espn_swid", ""), type="password"
        )
        st.sidebar.caption("Click 💾 Save My Settings below to store these on your account — see README for how to extract the cookies.")
        st.session_state["espn_s2"] = espn_s2
        st.session_state["espn_swid"] = espn_swid

    st.session_state["espn_league_id"] = espn_league_id
    st.session_state["espn_team_filter"] = espn_team_filter
    espn_ready = bool(espn_league_id) and bool(espn_s2) and bool(espn_swid)
    if espn_ready:
        st.sidebar.caption("✅ Connected")
    elif shared_espn_s2 and shared_espn_swid:
        st.sidebar.caption("❌ Not connected (needs League ID)")
    else:
        st.sidebar.caption("❌ Not connected (needs League ID + both cookies)")

    st.sidebar.divider()
    if st.sidebar.button("💾 Save My Settings", use_container_width=True, type="primary"):
        fields_to_save = {
            "sleeper_username": sleeper_username,
            "espn_league_id": espn_league_id,
            "espn_team_filter": espn_team_filter,
        }
        if not (shared_espn_s2 and shared_espn_swid):
            # Only store per-user cookies when there's no app-wide shared
            # pair -- no reason to duplicate the shared value into every
            # account, and it'd go stale if the owner ever rotates it.
            fields_to_save["espn_s2"] = espn_s2
            fields_to_save["espn_swid"] = espn_swid
        try:
            db.update_profile(supabase_cfg[0], supabase_cfg[1], st.session_state["username"], fields_to_save)
            st.sidebar.success("Saved.")
        except requests.RequestException as e:
            st.sidebar.error(f"Save failed: {e}")

    if st.sidebar.button("🔄 Sync All Platforms", use_container_width=True):
        fetch_sleeper_rosters.clear()
        fetch_espn_rosters.clear()
        fetch_nfl_state.clear()
        st.rerun()

    return {
        "season": season,
        "sleeper_username": sleeper_username,
        "espn_league_id": espn_league_id,
        "espn_team_filter": espn_team_filter,
        "espn_s2": espn_s2,
        "espn_swid": espn_swid,
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

    return all_leagues


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    supabase_cfg = get_supabase_config()
    if not supabase_cfg:
        st.title("🏈 Fantasy Football Command Center")
        st.error(
            "No database configured — add a `[supabase]` section (url, secret_key) to Secrets. See README."
        )
        return

    if not st.session_state.get("username"):
        render_login(supabase_cfg)
        return

    cfg = render_sidebar(supabase_cfg)
    st.title("🏈 Fantasy Football Command Center")

    all_leagues = load_all_leagues(cfg)
    df = leagues_to_dataframe(all_leagues)

    # KPI cards
    platforms_active = len({lg["platform"] for lg in all_leagues})
    leagues_connected = len(all_leagues)
    injury_flags = int((df["Injury Status"] != "ACTIVE").sum()) if not df.empty else 0
    top_exposure_df = compute_exposure(df)
    top_exposure = top_exposure_df.iloc[0]["Player"] if not top_exposure_df.empty else "—"

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total Rostered Players", len(df))
    k2.metric("Connected Leagues", leagues_connected, help="Target: 3")
    k3.metric("Platforms Active", platforms_active, help="Target: 2")
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
            my_players = lg.get("players", [])
            opp_players = lg.get("opponent_players", [])

            with st.container(border=True):
                st.markdown(f"**{lg['league_name']}** · {lg['platform']}" + (f" · Week {lg['week']}" if lg.get("week") else ""))

                col_mine, col_vs, col_opp = st.columns([5, 1, 5])
                col_mine.metric("Your Score", f"{my_pts:.1f}" if isinstance(my_pts, (int, float)) else "—")
                col_vs.markdown("<div style='text-align:center; padding-top:1.5rem;'>⚔️</div>", unsafe_allow_html=True)
                col_opp.metric(
                    f"{lg.get('opp_team_name', 'Opponent')}",
                    f"{opp_pts:.1f}" if isinstance(opp_pts, (int, float)) else "—",
                )

                col_mine, col_opp = st.columns(2)
                for col, label, players in ((col_mine, "🟢 Your Team", my_players), (col_opp, f"🔴 {lg.get('opp_team_name', 'Opponent')}", opp_players)):
                    with col:
                        starters = [p for p in players if p.get("starter")]
                        bench = [p for p in players if not p.get("starter")]

                        if not players:
                            st.caption(f"{label}: no roster data available.")
                            continue

                        st.markdown(f"**{label}**")
                        if starters:
                            st.caption("Starters")
                            st.dataframe(roster_table_df(starters), use_container_width=True, hide_index=True)
                        if bench:
                            with st.expander(f"Bench ({len(bench)})"):
                                st.dataframe(roster_table_df(bench), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
