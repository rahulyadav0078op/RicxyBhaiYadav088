"""
oauth_setup.py — one-time helper that mints a GOOGLE_REFRESH_TOKEN.

Use this when you want the bot to read files in *your* Drive without running a
browser on the server.

  1. Google Cloud Console -> create a project -> enable "Google Drive API"
  2. OAuth consent screen -> External -> add yourself as a test user
  3. Credentials -> Create credentials -> OAuth client ID -> "Desktop app"
  4. Download the JSON, save it here as client_secret.json
  5. python oauth_setup.py

The script prints the three lines to paste into .env. It requests read-only
Drive scope and stores nothing except the printed token.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCOPE = ["https://www.googleapis.com/auth/drive.readonly"]
SECRET_CANDIDATES = [
    "client_secret.json",
    "client_secrets.json",
    "credentials.json",
]


def main() -> int:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("pip install google-auth-oauthlib")
        return 1

    secret_path = next((p for p in SECRET_CANDIDATES if Path(p).is_file()), None)
    if len(sys.argv) > 1:
        secret_path = sys.argv[1]
    if not secret_path:
        print(
            "No client secret JSON found.\n"
            "Download it from Google Cloud Console (OAuth client -> Desktop app)\n"
            "and save it as client_secret.json, or pass the path as an argument."
        )
        return 1

    print(f"Using {secret_path}")
    flow = InstalledAppFlow.from_client_secrets_file(secret_path, SCOPE)
    creds = flow.run_local_server(port=0, prompt="consent")

    data = json.loads(Path(secret_path).read_text())
    installed = data.get("installed") or data.get("web") or {}
    client_id = installed.get("client_id", "")
    client_secret = installed.get("client_secret", "")

    print("\n--- paste these into .env -------------------------------------")
    print(f"GOOGLE_CLIENT_ID={client_id}")
    print(f"GOOGLE_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print("--------------------------------------------------------------")
    print("\nScopes granted:", " ".join(creds.scopes or SCOPE))
    print("Keep this output private — it is a live credential.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
