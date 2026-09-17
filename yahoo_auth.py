"""Yahoo Fantasy OAuth 2.0 helper.

Handles the full authorization-code lifecycle (authorize URL -> code -> tokens
-> refresh) and thin wrappers around the Yahoo Fantasy Sports REST API, so the
Streamlit app never has to shell out to a CLI or run a local callback server.
"""

from __future__ import annotations

import time
from urllib.parse import urlencode

import requests

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"


def get_authorization_url(client_id: str, redirect_uri: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "language": "en-us",
        # Without an explicit scope, Yahoo can hand back a token that looks
        # valid but 401s with oauth_problem=additional_authorization_required
        # on actual Fantasy Sports API calls, even if the app's own "API
        # Permissions" checkbox is set. fspt-r = Fantasy Sports read.
        "scope": "fspt-r",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _raise_with_body(resp: requests.Response) -> None:
    if not resp.ok:
        raise requests.HTTPError(
            f"{resp.status_code} {resp.reason} for url {resp.url}\nYahoo response body: {resp.text[:1000]}",
            response=resp,
        )


def exchange_code_for_tokens(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    # Yahoo's token endpoint expects client credentials via HTTP Basic Auth,
    # not as POST body fields — sending only body params silently yields a
    # token that later 401s on real API calls.
    resp = requests.post(
        TOKEN_URL,
        data={
            "redirect_uri": redirect_uri,
            "code": code,
            "grant_type": "authorization_code",
        },
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    _raise_with_body(resp)
    data = resp.json()
    data["obtained_at"] = time.time()
    return data


def refresh_access_token(refresh_token: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "redirect_uri": redirect_uri,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    _raise_with_body(resp)
    data = resp.json()
    data["obtained_at"] = time.time()
    if "refresh_token" not in data:
        data["refresh_token"] = refresh_token
    return data


def is_token_expired(token_data: dict | None) -> bool:
    if not token_data:
        return True
    obtained_at = token_data.get("obtained_at", 0)
    expires_in = token_data.get("expires_in", 3600)
    return time.time() > (obtained_at + expires_in - 60)


def ensure_valid_token(token_data: dict, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    """Returns a token dict guaranteed to have a live access_token, refreshing if needed."""
    if is_token_expired(token_data):
        return refresh_access_token(token_data["refresh_token"], client_id, client_secret, redirect_uri)
    return token_data


def _get(endpoint: str, access_token: str) -> dict:
    resp = requests.get(
        f"{FANTASY_BASE}/{endpoint}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"format": "json"},
        timeout=20,
    )
    _raise_with_body(resp)
    return resp.json()


def _flatten(items) -> dict:
    """Yahoo returns lists of single-key dicts for player/team metadata; merge them."""
    merged: dict = {}
    if isinstance(items, dict):
        items = [items]
    for item in items or []:
        if isinstance(item, dict):
            merged.update(item)
        elif isinstance(item, list):
            merged.update(_flatten(item))
    return merged


def fetch_yahoo_teams(access_token: str, season: int | str) -> list[dict]:
    """Returns the current user's teams for the NFL game in the given season.

    Each dict includes team_key, name, and league_key (derived from team_key,
    which is formatted as '{game_key}.l.{league_id}.t.{team_id}').
    """
    data = _get("users;use_login=1/games;game_keys=nfl/teams", access_token)
    teams: list[dict] = []
    try:
        user = data["fantasy_content"]["users"]["0"]["user"]
        games = user[1]["games"]
        for gkey, gval in games.items():
            if gkey == "count":
                continue
            game = gval["game"]
            game_meta = _flatten(game[0]) if isinstance(game, list) else game
            if str(game_meta.get("season")) != str(season):
                continue
            teams_container = game[1].get("teams", {}) if isinstance(game, list) and len(game) > 1 else {}
            for tkey, tval in teams_container.items():
                if tkey == "count":
                    continue
                team_info = _flatten(tval["team"][0] if isinstance(tval["team"], list) else tval["team"])
                team_key = team_info.get("team_key", "")
                league_key = ".".join(team_key.split(".")[:3]) if team_key else ""
                teams.append({
                    "team_key": team_key,
                    "team_id": team_info.get("team_id"),
                    "name": team_info.get("name", "My Team"),
                    "league_key": league_key,
                })
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return teams


def fetch_league_name(access_token: str, league_key: str) -> str:
    try:
        data = _get(f"league/{league_key}", access_token)
        league_info = _flatten(data["fantasy_content"]["league"][0])
        return league_info.get("name", league_key)
    except (KeyError, IndexError, TypeError, ValueError, requests.RequestException):
        return league_key


def fetch_yahoo_roster(access_token: str, team_key: str) -> list[dict]:
    """Returns normalized player dicts for a team's current roster."""
    data = _get(f"team/{team_key}/roster/players", access_token)
    players: list[dict] = []
    try:
        team = data["fantasy_content"]["team"]
        roster_players = team[1]["roster"]["0"]["players"]
        for pkey, pval in roster_players.items():
            if pkey == "count":
                continue
            player_arr = pval["player"][0]
            info = _flatten(player_arr)
            position = ""
            selected_pos = pval["player"][1].get("selected_position") if len(pval["player"]) > 1 else None
            if isinstance(selected_pos, list):
                position = _flatten(selected_pos).get("position", "")
            eligible = info.get("eligible_positions") or []
            if not position and eligible:
                first = eligible[0]
                position = first.get("position") if isinstance(first, dict) else str(first)
            status = info.get("status", "ACTIVE") or "ACTIVE"
            players.append({
                "player_id": info.get("player_id"),
                "name": info.get("name", {}).get("full", "Unknown"),
                "position": position or "?",
                "nfl_team": (info.get("editorial_team_abbr") or "FA").upper(),
                "injury_status": status.upper(),
            })
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return players


def fetch_matchup_opponent_roster(access_token: str, team_key: str, league_key: str, week: int) -> list[dict]:
    """Best-effort: finds this week's opponent team_key via the scoreboard, then fetches their roster."""
    try:
        data = _get(f"league/{league_key}/scoreboard;week={week}", access_token)
        matchups = data["fantasy_content"]["league"][1]["scoreboard"]["0"]["matchups"]
        for mkey, mval in matchups.items():
            if mkey == "count":
                continue
            teams = mval["matchup"]["0"]["teams"]
            team_keys = []
            for tkey, tval in teams.items():
                if tkey == "count":
                    continue
                info = _flatten(tval["team"][0])
                team_keys.append(info.get("team_key"))
            if team_key in team_keys:
                opponent_key = next((tk for tk in team_keys if tk != team_key), None)
                if opponent_key:
                    return fetch_yahoo_roster(access_token, opponent_key)
    except (KeyError, IndexError, TypeError, ValueError, requests.RequestException):
        pass
    return []
