"""
import_rclone_token.py — get Google Drive credentials WITHOUT touching Google
Cloud Console.

Background: private Drive files need OAuth credentials. Normally you make an
OAuth client in Google Cloud Console (free, ~5 min) and run oauth_setup.py.
If you'd rather skip the console entirely, rclone can do the browser dance and
hand you a ready refresh token, because rclone ships its own OAuth client:

    # one time, on your laptop
    sudo apt install rclone        # or: brew install rclone
    rclone config                  # n) new remote -> name: gdrive
                                   # storage: drive -> scope: drive.readonly
                                   # use auto config: y  (browser opens, Allow)
    # then:
    python import_rclone_token.py                    # reads default config
    python import_rclone_token.py ~/rclone.conf      # or an explicit path
    python import_rclone_token.py --remote gdrive2   # a differently named remote

It prints three lines to paste into .env (or Render/Railway/Heroku config):

    GOOGLE_CLIENT_ID=...
    GOOGLE_CLIENT_SECRET=...
    GOOGLE_REFRESH_TOKEN=...

Why this is safe: it is still YOUR account consenting in a browser to a
read-only scope. No password is stored anywhere and you can revoke it at
https://myaccount.google.com/permissions. Nothing here bypasses access control.

Caveat: rclone's client id is shared by every rclone user, so Google sometimes
rate-limits it. If you hit intermittent 403s at scale, make your own client
(oauth_setup.py) — it's free and takes five minutes.
"""

from __future__ import annotations

import configparser
import json
import os
import sys
from pathlib import Path

DEFAULT_PATHS = [
    Path.home() / ".config" / "rclone" / "rclone.conf",
    Path.home() / ".rclone.conf",
    Path(os.getenv("APPDATA", "")) / "rclone" / "rclone.conf",
]


def find_config() -> Path | None:
    for path in DEFAULT_PATHS:
        if path.is_file():
            return path
    return None


def parse(path: Path, remote: str | None = None) -> tuple[str, str, str, str]:
    """Return (remote_name, client_id, client_secret, refresh_token)."""
    parser = configparser.ConfigParser()
    parser.read(path)

    names = [s for s in parser.sections()]
    if not names:
        raise SystemExit(f"No remotes found in {path}")

    if remote:
        if remote not in names:
            raise SystemExit(f"Remote '{remote}' not in {path}. Available: {names}")
        name = remote
    else:
        drive_remotes = [n for n in names if parser[n].get("type") == "drive"]
        if not drive_remotes:
            raise SystemExit(f"No 'type = drive' remote in {path}. Found: {names}")
        name = drive_remotes[0]

    section = parser[name]
    client_id = section.get("client_id", "").strip()
    client_secret = section.get("client_secret", "").strip()

    raw_token = section.get("token", "").strip()
    if not raw_token:
        raise SystemExit(f"Remote '{name}' has no token — run `rclone config reconnect {name}:`")

    try:
        token = json.loads(raw_token)
    except ValueError as exc:
        raise SystemExit(f"Token in '{name}' is not valid JSON: {exc}") from exc

    refresh = token.get("refresh_token", "").strip()
    if not refresh:
        # rclone stores short-lived access tokens for some backends
        raise SystemExit(
            f"Remote '{name}' has no refresh_token. Re-run `rclone config reconnect {name}:`."
        )

    scope = section.get("scope", "drive.readonly")
    if scope not in ("drive", "drive.readonly"):
        print(
            f"! Remote scope is '{scope}'. Read-only or full drive scope is what "
            "this bot needs.",
            file=sys.stderr,
        )
    if not client_id or not client_secret:
        raise SystemExit(
            f"Remote '{name}' has no client_id/client_secret. Run "
            f"`rclone config update {name}: client_id YOUR_ID client_secret YOUR_SECRET`, "
            "or use oauth_setup.py."
        )
    return name, client_id, client_secret, refresh


def main() -> int:
    args = [a for a in sys.argv[1:]]
    remote = None
    if "--remote" in args:
        i = args.index("--remote")
        remote = args[i + 1]
        del args[i : i + 2]

    path = Path(args[0]).expanduser() if args else find_config()
    if not path or not path.is_file():
        print(
            "rclone config not found.\n"
            "Create one first:\n"
            "  rclone config      # n) new remote -> gdrive -> drive -> scope drive.readonly\n"
            "Then re-run this script, or pass the path explicitly.",
            file=sys.stderr,
        )
        return 1

    name, client_id, client_secret, refresh = parse(path, remote)
    print(f"# remote '{name}' from {path}", file=sys.stderr)
    print("# paste these into .env (or your host's env vars)")
    print(f"GOOGLE_CLIENT_ID={client_id}")
    print(f"GOOGLE_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={refresh}")
    print("# keep this output private — it is a live credential", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
