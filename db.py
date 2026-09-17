"""User accounts + saved team settings, stored in Supabase (Postgres).

Talks to Supabase's REST API (PostgREST) directly over HTTP instead of
pulling in the official supabase-py SDK, to keep dependencies limited to
requests (already used everywhere else in this app).

Security note: passwords are hashed (salted SHA-256) but there is no email
verification, password reset, or rate limiting. This is intentionally
lightweight for a small trusted group of testers, not a hardened auth
system.
"""

from __future__ import annotations

import hashlib
import secrets as secrets_lib

import requests

TABLE = "user_profiles"
PROFILE_FIELDS = ("sleeper_username", "espn_league_id", "espn_team_filter", "espn_s2", "espn_swid")


class UsernameTakenError(Exception):
    pass


def _headers(service_key: str, prefer: str | None = None) -> dict:
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _base_url(supabase_url: str) -> str:
    return f"{supabase_url.rstrip('/')}/rest/v1/{TABLE}"


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets_lib.token_hex(16)
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return digest, salt


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    digest, _ = hash_password(password, salt)
    return digest == expected_hash


def get_user(supabase_url: str, service_key: str, username: str) -> dict | None:
    resp = requests.get(
        _base_url(supabase_url),
        headers=_headers(service_key),
        params={"username": f"eq.{username}", "select": "*"},
        timeout=15,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


def create_user(supabase_url: str, service_key: str, username: str, password: str) -> dict:
    username = username.strip()
    if get_user(supabase_url, service_key, username):
        raise UsernameTakenError(f"Username '{username}' is already taken.")

    password_hash, salt = hash_password(password)
    payload = {
        "username": username,
        "password_hash": password_hash,
        "password_salt": salt,
        **{field: "" for field in PROFILE_FIELDS},
    }
    resp = requests.post(
        _base_url(supabase_url),
        headers=_headers(service_key, prefer="return=representation"),
        json=payload,
        timeout=15,
    )
    if resp.status_code == 409:
        raise UsernameTakenError(f"Username '{username}' is already taken.")
    resp.raise_for_status()
    return resp.json()[0]


def authenticate(supabase_url: str, service_key: str, username: str, password: str) -> dict | None:
    user = get_user(supabase_url, service_key, username.strip())
    if not user:
        return None
    if not verify_password(password, user["password_salt"], user["password_hash"]):
        return None
    return user


def update_profile(supabase_url: str, service_key: str, username: str, fields: dict) -> None:
    body = {k: v for k, v in fields.items() if k in PROFILE_FIELDS}
    resp = requests.patch(
        _base_url(supabase_url),
        headers=_headers(service_key, prefer="return=minimal"),
        params={"username": f"eq.{username}"},
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
