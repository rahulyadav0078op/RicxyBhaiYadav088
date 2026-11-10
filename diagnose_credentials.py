"""
diagnose_credentials.py — find out exactly why Google rejects your credentials.

`invalid_client: The OAuth client was not found` means Google does not recognise
the client_id you sent. There are only a handful of causes, and this script tells
you which one you have:

  invalid_client  -> client_id wrong / deleted / has quotes+spaces / token was
                     minted for a DIFFERENT client (e.g. Playground's built-in one)
  invalid_grant   -> client is fine, but the refresh token is dead, revoked, or
                     was issued to another client / another Google account
  invalid_scope   -> scope mismatch
  200 OK          -> credentials work; prints the scopes and the client they belong to

Usage:
    python diagnose_credentials.py                # reads .env then environment
    python diagnose_credentials.py --env-only     # ignore shell env
    python diagnose_credentials.py --id X --secret Y --token Z

Safe to share its output — it masks every secret.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_URI = "https://oauth2.googleapis.com/token"


# --------------------------------------------------------------------------- #
# env loading (no dependency on python-dotenv being installed)
# --------------------------------------------------------------------------- #
def load_env(path: Path = Path(".env")) -> dict[str, str]:
    data: dict[str, str] = {}
    if path.is_file():
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            data[key.strip()] = value.strip()
    return data


def pick(name: str, env_file: dict[str, str], env_only: bool, cli: dict[str, str]) -> str:
    if name.lower() in cli:
        return cli[name.lower()]
    if not env_only:
        value = os.environ.get(name, "")
        if value:
            return value
    return env_file.get(name, "")


# --------------------------------------------------------------------------- #
# format checks: catch the boring stuff before blaming Google
# --------------------------------------------------------------------------- #
def audit(name: str, value: str) -> list[str]:
    problems: list[str] = []
    if not value:
        problems.append("MISSING — variable is empty or absent")
        return problems

    if value != value.strip():
        problems.append("has leading/trailing whitespace")
    if value.startswith(("'", '"')) or value.endswith(("'", '"')):
        problems.append(
            "wrapped in quotes — Railway/Render paste this literally, so the "
            "quote characters become part of the value. Remove them."
        )
    if "\n" in value or "\r" in value:
        problems.append("contains a newline (multi-line paste)")
    if " " in value and not name.endswith("_JSON"):
        problems.append("contains a space — likely a bad copy")
    if value.lower().startswith(("goog_", "your_", "xxx", "...")):
        problems.append("looks like a placeholder, not a real value")

    if name == "GOOGLE_CLIENT_ID":
        if not re.match(r"^\d{6,}-[a-z0-9]+\.apps\.googleusercontent\.com$", value):
            problems.append(
                "does not look like a client id "
                "(expected 1234567890-abc123.apps.googleusercontent.com). "
                "Common mistake: pasting the client *name*, the project number, "
                "or the project id instead."
            )
        if value.startswith("407408718192"):
            problems.append(
                "this is the OAuth Playground's built-in client, not yours — "
                "tokens minted with it will not work with your own client"
            )
    if name == "GOOGLE_CLIENT_SECRET":
        if value.startswith("GOCSPX-") is False:
            problems.append(
                "does not start with GOCSPX- (Desktop/Web client secrets do) — "
                "check you did not paste the client id here"
            )
        if len(value) < 20:
            problems.append("suspiciously short for a client secret")
    if name == "GOOGLE_REFRESH_TOKEN":
        if not value.startswith("1//"):
            problems.append(
                "does not start with '1//' — that is the shape of a Google "
                "refresh token. Did you paste the access_token (ya29…) instead?"
            )
        if value.startswith("ya29."):
            problems.append(
                "this is an ACCESS token (valid ~1 hour), not a refresh token"
            )
    return problems


def mask(value: str, keep: int = 6) -> str:
    if not value:
        return "(empty)"
    if len(value) <= keep * 2:
        return value[:2] + "…" + value[-2:]
    return f"{value[:keep]}…{value[-4:]} (len {len(value)})"


# --------------------------------------------------------------------------- #
# the actual Google calls
# --------------------------------------------------------------------------- #
def exchange(client_id: str, client_secret: str, refresh_token: str) -> tuple[int, dict]:
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode()
    req = urllib.request.Request(
        TOKEN_URI,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"raw": raw[:400]}
    except Exception as exc:  # noqa: BLE001
        return 0, {"network_error": repr(exc)}


def token_info(access_token: str) -> dict:
    url = "https://oauth2.googleapis.com/tokeninfo?" + urllib.parse.urlencode(
        {"access_token": access_token}
    )
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)}


DIAGNOSIS = {
    "invalid_client": [
        "Google does not recognise this client_id. Fix in this order:",
        "1. Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client IDs:",
        "   does the client still exist? If you deleted/recreated it, the old id is dead.",
        "2. Copy the **Client ID** from that row again (ends with "
        ".apps.googleusercontent.com). Do not copy the name or project number.",
        "3. Make sure there are no quotes/spaces around the value in your host's "
        "env vars — a pasted quote becomes part of the string.",
        "4. Mint a NEW refresh token in the Playground with YOUR client: "
        "gear ⚙ → tick 'Use your own OAuth credentials' → paste id+secret → then "
        "Authorize APIs again.",
    ],
    "invalid_grant": [
        "The client is valid, but the refresh token is not usable.",
        "1. If you revoked access (or the app is in Testing mode and the token is "
        ">7 days old), mint a new one.",
        "2. The token must be minted with the SAME client id/secret that you are "
        "using here. A token from the Playground's built-in client (407408718192…) "
        "will never work with your own client.",
        "3. Confirm you authorised the same Google account whose Drive you want.",
    ],
    "unauthorized_client": [
        "The client type does not allow this grant.",
        "Create the OAuth client as type **Desktop app** (or Web app with the "
        "Playground redirect URI), then re-mint the token.",
    ],
    "invalid_scope": [
        "Scope mismatch — re-authorise with https://www.googleapis.com/auth/drive.readonly.",
    ],
    "deleted_client": [
        "The OAuth client was deleted. Create a new one and re-mint the token.",
    ],
}


def main() -> int:
    args = sys.argv[1:]
    env_only = "--env-only" in args
    cli: dict[str, str] = {}
    for flag, key in (("--id", "google_client_id"), ("--secret", "google_client_secret"),
                      ("--token", "google_refresh_token")):
        if flag in args:
            cli[key] = args[args.index(flag) + 1]

    env_file = load_env()
    cid = pick("GOOGLE_CLIENT_ID", env_file, env_only, cli)
    secret = pick("GOOGLE_CLIENT_SECRET", env_file, env_only, cli)
    token = pick("GOOGLE_REFRESH_TOKEN", env_file, env_only, cli)

    print("── credential audit ──────────────────────────────────────────")
    all_problems: list[str] = []
    for name, value in (
        ("GOOGLE_CLIENT_ID", cid),
        ("GOOGLE_CLIENT_SECRET", secret),
        ("GOOGLE_REFRESH_TOKEN", token),
    ):
        problems = audit(name, value)
        status = "✓" if not problems else "✗"
        print(f"{status} {name:<22} {mask(value)}")
        for problem in problems:
            print(f"    ! {problem}")
        all_problems.extend(f"{name}: {p}" for p in problems)

    if not (cid and secret and token):
        print("\nMissing values — fill .env (or pass --id/--secret/--token).")
        return 1

    print("\n── live token refresh ───────────────────────────────────────")
    status, payload = exchange(cid.strip().strip("'\""), secret.strip().strip("'\""),
                              token.strip().strip("'\""))

    if status == 200:
        access = payload.get("access_token", "")
        print("✓ Google accepted the credentials — refresh works.")
        info = token_info(access) if access else {}
        scope = info.get("scope") or payload.get("scope", "")
        print(f"  scopes : {scope}")
        if "drive.readonly" in scope or "auth/drive" in scope:
            print("  ✓ Drive read access present")
        else:
            print("  ✗ Drive scope missing — re-authorise with drive.readonly")
        aud = info.get("aud") or info.get("azp")
        if aud:
            match = "✓ matches" if aud == cid.strip().strip("'\"") else "✗ DIFFERENT"
            print(f"  token issued to client: {aud}  ({match})")
        print("  → credentials are fine; restart the bot and run /auth")
        return 0

    error = payload.get("error", f"HTTP {status}")
    print(f"✗ failed: {error}")
    if payload.get("error_description"):
        print(f"  {payload['error_description']}")
    if payload.get("network_error"):
        print(f"  network: {payload['network_error']}")

    print()
    for line in DIAGNOSIS.get(error, ["See https://developers.google.com/identity/protocols/oauth2"]):
        print(f"  {line}")

    if error == "invalid_client" and cid.startswith("407408718192"):
        print(
            "\n  ⚠ You are using the Playground's shared client. Mint the token with "
            "your own client id/secret instead."
        )
    if all_problems:
        print("\n  Format problems found above are the likely cause — fix them first.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
