"""
oauth_helper.py — get a GOOGLE_REFRESH_TOKEN without installing anything heavy.

WHY THIS EXISTS
---------------
The bot's private-file mode needs three values: GOOGLE_CLIENT_ID,
GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN. Getting the first two is easy
(Google Cloud Console -> Credentials). Getting the third is where everyone gets
stuck, because it means completing an OAuth consent flow *once*.

You never send this bot your Google password, and it never asks for one. What
happens instead: you sign in **on google.com**, Google shows you a consent
screen, and Google hands this tool a one-time code. The tool trades that code
for a refresh token. The token can only do what the scope says (Drive read).

Two ways to run it:

  1) On the same computer, browser available (easiest):
         python oauth_helper.py

     It opens the consent page, listens on http://127.0.0.1:8765 for the
     redirect, and prints the three .env lines.

  2) Browser on a different device / headless (phone -> laptop, VPS, etc.):
         python oauth_helper.py --manual

     It prints the consent URL. Open it anywhere, sign in, and after Google
     redirects you the address bar contains `...?code=4/0AX...`. Copy that
     `code` value and paste it back into the terminal.

Only standard library is used, so this works even in a bare Python install.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

SCOPES = {
    "readonly": "https://www.googleapis.com/auth/drive.readonly",
    "full": "https://www.googleapis.com/auth/drive",
}

SECRET_CANDIDATES = [
    "client_secret.json",
    "client_secrets.json",
    "credentials.json",
    "client_secret_*.json",
]


# --------------------------------------------------------------------------- #
# Client secret discovery
# --------------------------------------------------------------------------- #
def find_client_secret(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            sys.exit(f"✗ {path} not found")
        return path
    for name in SECRET_CANDIDATES:
        if "*" in name:
            matches = sorted(Path(".").glob(name))
            if matches:
                return matches[0]
            continue
        candidate = Path(name)
        if candidate.is_file():
            return candidate
    sys.exit(
        "✗ No OAuth client JSON found.\n\n"
        "  Google Cloud Console → APIs & Services → Credentials →\n"
        "  Create credentials → OAuth client ID → application type **Desktop app**\n"
        "  → Download JSON → save it next to this script as client_secret.json\n\n"
        "  (Or pass the path: python oauth_helper.py --client /path/to/file.json)"
    )


def read_client(path: Path) -> tuple[str, str]:
    try:
        data = json.loads(path.read_text())
    except ValueError as exc:
        sys.exit(f"✗ {path} is not valid JSON: {exc}")
    block = data.get("installed") or data.get("web") or data
    cid = (block.get("client_id") or "").strip()
    secret = (block.get("client_secret") or "").strip()
    if not cid or not secret:
        sys.exit(
            f"✗ {path} has no client_id/client_secret. Download the JSON from "
            "Credentials → OAuth client ID, don't hand-write it."
        )
    if "web" in data:
        print(
            "! This is a *Web application* client. Google only accepts redirect\n"
            "  URIs that are registered on it. Add http://127.0.0.1:8765/ under\n"
            "  Credentials → your client → Authorized redirect URIs, or create a\n"
            "  **Desktop app** client instead (loopback needs no registration).\n"
            "  A Desktop client is what this script expects.\n"
        )
    return cid, secret


# --------------------------------------------------------------------------- #
# Flow
# --------------------------------------------------------------------------- #
def build_auth_url(client_id: str, redirect_uri: str, scope: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "access_type": "offline",   # <- this is what yields a refresh token
        "prompt": "consent",        # <- force a refresh token even on re-auth
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode()
    req = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")
        try:
            parsed = json.loads(detail)
            msg = f"{parsed.get('error')}: {parsed.get('error_description')}"
        except ValueError:
            msg = detail[:400]
        sys.exit(
            f"✗ Token exchange failed ({exc.code}).\n  {msg}\n\n"
            "  Most common cause: the code was already used (they are single\n"
            "  use and expire in ~10 minutes), or the redirect URI does not\n"
            "  match the one registered for this client. Re-run the script."
        )


def print_env(client_id: str, client_secret: str, tokens: dict, scope_label: str) -> None:
    refresh = tokens.get("refresh_token", "")
    if not refresh:
        sys.exit(
            "✗ Google returned no refresh_token.\n"
            "  That happens when this app was already authorized and the\n"
            "  consent screen was skipped. Revoke the app at\n"
            "  https://myaccount.google.com/permissions and run again."
        )
    print("\n" + "=" * 68)
    print("  Paste these three lines into your host's environment variables")
    print("  (Render/Railway → Variables tab) or into .env")
    print("=" * 68)
    print(f"GOOGLE_CLIENT_ID={client_id}")
    print(f"GOOGLE_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={refresh}")
    if scope_label == "full":
        print("DRIVE_SCOPE=full          # enables /quotafix and /streamdl copies")
    print("=" * 68)
    print(f"\nScope granted : {tokens.get('scope', SCOPES[scope_label])}")
    print(
        "\n⚠️  This is a live credential. Treat it like a password.\n"
        "⚠️  If your OAuth app is in 'Testing' mode Google expires refresh\n"
        "    tokens after 7 days. Publish the app (OAuth consent screen →\n"
        "    Publish app) to make it permanent.\n"
    )


def run_local_server(client_id: str, client_secret: str, scope: str, state: str, port: int) -> str | None:
    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state", [""])[0] != state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"State mismatch - close this tab and retry.")
                return
            if "error" in query:
                result["error"] = query["error"][0]
            else:
                result["code"] = query.get("code", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<h2>Done.</h2><p>You can close this tab and go back to the "
                b"terminal.</p>"
            )
            done.set()

        def log_message(self, *_args):  # silence the default noisy logger
            return

    redirect_uri = f"http://127.0.0.1:{port}/"
    url = build_auth_url(client_id, redirect_uri, scope, state)
    try:
        server = HTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        print(f"! port {port} unavailable ({exc}); falling back to --manual mode.\n")
        return None

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print("Opening your browser to authorize Drive read access…")
    print("If nothing opens, paste this URL manually:\n\n" + url + "\n")
    webbrowser.open(url)

    if not done.wait(timeout=300):
        server.shutdown()
        print("\n! Timed out waiting for the browser. Re-run with --manual.")
        return None
    server.shutdown()
    if "error" in result:
        sys.exit(f"✗ Authorization was denied: {result['error']}")
    return result.get("code") or None


def parse_code_from_pasted(text: str) -> tuple[str, str | None]:
    """Pull the authorization code out of whatever the user pasted.

    Google removed the out-of-band flow (`urn:ietf:wg:oauth:2.0:oob`) in 2022,
    so there is no page that simply *shows* a code any more. What happens with
    a loopback redirect on a phone is:

        browser -> https://accounts.google.com/...  (consent)
                -> http://127.0.0.1:8765/?code=4/0AX...&scope=...
                   (page fails to load, because the listener is not on the phone)

    The address bar still holds the full URL, so we ask for that URL instead of
    a bare code. This function accepts either form, which also makes it usable
    from a Colab cell where the user just pastes the whole line.

    Returns (code, state_or_None); raises ValueError when nothing usable is in
    the text — passing garbage to the token endpoint produces a confusing
    "invalid_grant" otherwise.
    """
    text = (text or "").strip().strip('"').strip("'")
    if not text:
        raise ValueError("Nothing was pasted.")

    # A full redirect URL (or just its query string) — parse it properly.
    if "code=" in text or "error=" in text:
        candidate = text
        if "://" not in candidate:
            # bare "?code=..." or "code=..."
            candidate = "http://127.0.0.1/?" + candidate.lstrip("?")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(candidate).query)
        if "error" in query:
            raise ValueError(f"Google returned an error instead of a code: {query['error'][0]}")
        code = (query.get("code") or [""])[0]
        state = (query.get("state") or [None])[0]
        if code:
            return code, state
        raise ValueError("That URL has no `code` parameter — re-run the authorization step.")

    # A bare code: OAuth codes look like 4/0AX4Xf... and contain a '/'.
    cleaned = text.replace(" ", "")
    if cleaned.startswith("4/") and len(cleaned) > 10:
        return cleaned, None
    raise ValueError(
        "That does not look like an authorization code or redirect URL.\n"
        "Paste the FULL address-bar URL (it contains `?code=`) or the code that "
        "starts with `4/`."
    )


def run_manual(client_id: str, scope_label: str, port: int = 8765) -> dict:
    """Loopback redirect, but the user pastes the result — no listener needed.

    This is the only manual flow Google still supports: the OOB (`oob`) redirect
    was removed in October 2022, so "Google shows you a code" no longer exists.
    """
    redirect_uri = f"http://127.0.0.1:{port}/"
    state = secrets.token_urlsafe(8)
    url = build_auth_url(client_id, redirect_uri, scope_label, state)
    print(
        "\n1) Open this URL on ANY device, signed in to the Google account whose\n"
        "   Drive the bot should read:\n\n" + url + "\n"
    )
    print(
        "2) Approve the request. Google will try to send you to\n"
        f"   {redirect_uri} and the page will FAIL to load.\n"
        "   That is expected — the address bar still holds the answer.\n"
    )
    if sys.platform.startswith("win") or sys.platform == "darwin":
        print("   Tip: on desktop, right-click the address bar -> Copy.\n")
    else:
        print("   Tip: tap the address bar, select all, copy.\n")

    pasted = input("3) Paste the FULL address-bar URL here (or just the code): ")
    code, returned_state = parse_code_from_pasted(pasted)
    if returned_state and returned_state != state:
        print("! state did not match — continuing, but this usually means an old URL was pasted")
    return {"code": code, "redirect_uri": redirect_uri}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--client", help="path to the OAuth client JSON")
    ap.add_argument("--full", action="store_true", help="request full Drive scope (enables /quotafix)")
    ap.add_argument("--manual", action="store_true", help="no local server; paste the code")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    client_path = find_client_secret(args.client)
    client_id, client_secret = read_client(client_path)
    scope_label = "full" if args.full else "readonly"
    scope = SCOPES[scope_label]

    print(f"Using {client_path}")
    print(f"Scope: {scope}\n")

    state = secrets.token_urlsafe(16)
    redirect_uri = f"http://127.0.0.1:{args.port}/"

    if args.manual:
        info = run_manual(client_id, scope_label, args.port)
        code, redirect_uri = info["code"], info["redirect_uri"]
    else:
        code = run_local_server(client_id, client_secret, scope, state, args.port)
        if code is None:
            info = run_manual(client_id, scope_label, args.port)
            code, redirect_uri = info["code"], info["redirect_uri"]

    print("\nExchanging the code for a refresh token…")
    tokens = exchange_code(client_id, client_secret, code, redirect_uri)
    if "refresh_token" not in tokens:
        tokens = dict(tokens)
    print_env(client_id, client_secret, tokens, scope_label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
