"""
setup_wizard.py — one command, guided setup, no Google Cloud Console needed.

It offers three paths and validates whatever you pick before writing .env:

  1. rclone  (NO Cloud Console, NO Playground, NO redirect URI)
     rclone ships its own OAuth client, so Google just sees a normal consent
     screen. The wizard runs rclone, imports the refresh token it produces,
     tests it against Google, and writes .env for you.

  2. Own OAuth client (Cloud Console, 5 min)
     For people who want their own quota instead of rclone's shared client.

  3. Public links only (nothing to configure)

Usage:
    python setup_wizard.py                 # interactive
    python setup_wizard.py --check-only    # just validate what is in .env
    python setup_wizard.py --host render   # print env block for Render/Railway
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_URI = "https://oauth2.googleapis.com/token"
DRIVE_API = "https://www.googleapis.com/drive/v3"
ENV_PATH = Path(".env")
SCOPE = "https://www.googleapis.com/auth/drive.readonly"

BOLD, DIM, GREEN, RED, YELLOW, RESET = (
    "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[0m"
)


def say(msg: str = "") -> None:
    print(msg)


def step(n: int, msg: str) -> None:
    print(f"\n{BOLD}[{n}]{RESET} {msg}")


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def bad(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET} {msg}")


# --------------------------------------------------------------------------- #
# env file handling
# --------------------------------------------------------------------------- #
def merge_env(path: Path, values: dict[str, str]) -> Path:
    """Write/update keys in .env without touching unrelated lines or comments."""
    lines: list[str] = path.read_text().splitlines() if path.is_file() else []
    out: list[str] = []
    written: set[str] = set()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in values:
            out.append(f"{key}={values[key]}")
            written.add(key)
        else:
            out.append(line)

    for key, value in values.items():
        if key not in written:
            out.append(f"{key}={value}")

    path.write_text("\n".join(out).rstrip() + "\n")
    try:
        path.chmod(0o600)  # credentials in a readable-by-all file is a footgun
    except OSError:
        pass
    return path


def env_block(values: dict[str, str], host: str) -> str:
    """Paste-ready block for a cloud host's env var editor."""
    lines = [f"# --- paste into {host} → Environment / Variables ---"]
    for key, value in values.items():
        if value:
            lines.append(f"{key}={value}")
    if host.lower() in {"render", "railway"}:
        lines += [
            "",
            "# paths (Render: disk mounted at /data)",
            "DATA_DIR=/data",
            "TEMP_DIR=/data/tmp",
            "JOBS_DB=/data/jobs.sqlite3",
            "FOLDER_ORDER=deep",
            "MAX_DOWNLOAD_GB=15",
            "MAX_UPLOAD_MB=2000",
            "CONCURRENT_DOWNLOADS=1",
        ]
    return "\n".join(lines)


def clean(value: str) -> str:
    return (value or "").strip().strip("'\"").strip()


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def test_credentials(client_id: str, client_secret: str, refresh_token: str) -> tuple[bool, str]:
    """Exchange the refresh token. Returns (ok, human message)."""
    body = urllib.parse.urlencode(
        {
            "client_id": clean(client_id),
            "client_secret": clean(client_secret),
            "refresh_token": clean(refresh_token),
            "grant_type": "refresh_token",
        }
    ).encode()
    req = urllib.request.Request(
        TOKEN_URI, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            err = json.loads(raw)
        except ValueError:
            return False, raw[:300]
        code = err.get("error", f"HTTP {exc.code}")
        hint = {
            "invalid_client": "client id/secret do not match the token (or the client was deleted)",
            "invalid_grant": "refresh token is dead/revoked/expired — mint a new one",
            "unauthorized_client": "create the client as type 'Desktop app'",
        }.get(code, "")
        return False, f"{code}: {err.get('error_description', '')} {('→ ' + hint) if hint else ''}".strip()
    except Exception as exc:  # noqa: BLE001
        return False, f"network error: {exc!r}"

    token = payload.get("access_token", "")
    scopes = payload.get("scope", "")
    if token:
        info_req = urllib.request.Request(
            "https://oauth2.googleapis.com/tokeninfo?"
            + urllib.parse.urlencode({"access_token": token})
        )
        try:
            with urllib.request.urlopen(info_req, timeout=20) as resp:
                info = json.loads(resp.read().decode())
            scopes = info.get("scope", scopes)
        except Exception:  # noqa: BLE001
            pass
    if "drive" not in scopes:
        return False, f"token works but has no Drive scope (got: {scopes or 'none'})"
    return True, scopes


def can_list_folder(client_id: str, client_secret: str, refresh_token: str, folder_id: str) -> tuple[bool, str]:
    """Prove end-to-end Drive access by listing the folder the user cares about."""
    body = urllib.parse.urlencode(
        {
            "client_id": clean(client_id),
            "client_secret": clean(client_secret),
            "refresh_token": clean(refresh_token),
            "grant_type": "refresh_token",
        }
    ).encode()
    try:
        req = urllib.request.Request(
            TOKEN_URI, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            token = json.loads(resp.read().decode())["access_token"]
        query = urllib.parse.urlencode(
            {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "files(id,name,mimeType),nextPageToken",
                "pageSize": "5",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
        )
        api = urllib.request.Request(
            f"{DRIVE_API}/files?{query}", headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(api, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        files = data.get("files", [])
        names = ", ".join(f.get("name", "?") for f in files[:3])
        return True, f"{len(files)}+ entries visible ({names}…)" if files else "folder visible but empty"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code} — folder not shared with this account?"
    except Exception as exc:  # noqa: BLE001
        return False, f"network error: {exc!r}"


# --------------------------------------------------------------------------- #
# rclone path (no Cloud Console)
# --------------------------------------------------------------------------- #
def rclone_available() -> bool:
    return shutil.which("rclone") is not None


def rclone_config_create(remote: str = "gdrive") -> int:
    """Create + authorize a read-only Drive remote. Opens a browser (or URL)."""
    cmd = [
        "rclone", "config", "create", remote, "drive",
        "scope", "drive.readonly",
        "config_is_local", "true",
    ]
    say(f"{DIM}$ {' '.join(cmd)}{RESET}")
    say("  (browser khulega / URL print hoga — apna Gmail chuno → Allow)")
    return subprocess.call(cmd)


def import_from_rclone(remote: str | None = None) -> tuple[str, str, str]:
    """Parse rclone.conf via the existing importer, return (id, secret, token)."""
    args = [sys.executable, "import_rclone_token.py"]
    if remote:
        args += ["--remote", remote]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "import_rclone_token.py failed")
    values = {}
    for line in result.stdout.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    missing = [
        k
        for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")
        if not values.get(k)
    ]
    if missing:
        raise RuntimeError(f"could not read {', '.join(missing)} from rclone config")
    return (
        values["GOOGLE_CLIENT_ID"],
        values["GOOGLE_CLIENT_SECRET"],
        values["GOOGLE_REFRESH_TOKEN"],
    )


# --------------------------------------------------------------------------- #
# flows
# --------------------------------------------------------------------------- #
def finish(client_id: str, client_secret: str, refresh_token: str, folder_id: str, host: str) -> int:
    step(3, "Google se verify kar rahe hain…")
    good, message = test_credentials(client_id, client_secret, refresh_token)
    if not good:
        bad(message)
        warn("Credentials save nahi kiye. Upar wali galti theek karke dobara chalao.")
        return 1
    ok(f"token valid, scope: {message}")

    if folder_id:
        listing_ok, listing_msg = can_list_folder(
            client_id, client_secret, refresh_token, folder_id
        )
        (ok if listing_ok else bad)(f"folder check: {listing_msg}")

    step(4, ".env likh rahe hain")
    values = {
        "GOOGLE_CLIENT_ID": clean(client_id),
        "GOOGLE_CLIENT_SECRET": clean(client_secret),
        "GOOGLE_REFRESH_TOKEN": clean(refresh_token),
    }
    if not ENV_PATH.is_file():
        example = Path(".env.example")
        if example.is_file():
            ENV_PATH.write_text(example.read_text())
    merge_env(ENV_PATH, values)
    ok(f"{ENV_PATH.resolve()} updated (chmod 600)")

    say("\n" + env_block(values, host))
    say(
        f"\n{DIM}Agla step: ye block host ke Environment/Variables me paste karo "
        f"→ redeploy → Telegram pe /auth → folder link bhejo.{RESET}"
    )
    return 0


def flow_rclone(host: str) -> int:
    step(1, "rclone check kar rahe hain")
    if not rclone_available():
        bad("rclone installed nahi hai.")
        say("  Install:  sudo apt install rclone      (macOS: brew install rclone)")
        say("  Android:  Termux me  pkg install rclone")
        say(f"  {DIM}Phir ye wizard dobara chalao.{RESET}")
        return 1
    ok("rclone mil gaya")

    step(2, "Google Drive authorize kar rahe hain (Cloud Console ki zaroorat nahi)")
    if rclone_config_create() != 0:
        bad("rclone authorize fail hua. `rclone config` manually chala ke dobara try karo.")
        return 1
    try:
        client_id, secret, token = import_from_rclone()
    except RuntimeError as exc:
        bad(str(exc))
        return 1
    ok("refresh token mil gaya (rclone ke client se)")

    folder_id = input("\nCourse folder ID (optional, Enter = skip): ").strip()
    return finish(client_id, secret, token, folder_id, host)


def flow_own_client(host: str) -> int:
    step(1, "Apna OAuth client (Cloud Console)")
    say(
        "  1. console.cloud.google.com → Drive API enable\n"
        "  2. OAuth consent screen → External → scope drive.readonly → Publish app\n"
        "  3. Credentials → Create credentials → OAuth client ID → "
        f"{BOLD}Desktop app{RESET}\n"
        "  4. Client ID + secret copy karo\n"
        "  5. python oauth_setup.py  (browser me Allow → refresh token milega)"
    )
    client_id = input("\nGOOGLE_CLIENT_ID: ").strip()
    secret = input("GOOGLE_CLIENT_SECRET: ").strip()
    token = input("GOOGLE_REFRESH_TOKEN: ").strip()
    if not (client_id and secret and token):
        bad("teeno values chahiye")
        return 1
    folder_id = input("Course folder ID (optional, Enter = skip): ").strip()
    return finish(client_id, secret, token, folder_id, host)


def flow_public(host: str) -> int:
    step(1, "Public-only mode")
    say(
        "  Koi credentials nahi. Bot sirf wo files bhejega jo owner ne\n"
        "  'anyone with the link' share ki hain. Folder scan nahi hoga\n"
        "  (Google anonymous clients ko listing nahi deta) — sirf single file links."
    )
    warn("Private/restricted course files is mode me nahi milenge.")
    return 0


def check_only(host: str) -> int:
    from diagnose_credentials import load_env

    env = load_env()
    cid = clean(env.get("GOOGLE_CLIENT_ID", "") or os.environ.get("GOOGLE_CLIENT_ID", ""))
    secret = clean(env.get("GOOGLE_CLIENT_SECRET", "") or os.environ.get("GOOGLE_CLIENT_SECRET", ""))
    token = clean(env.get("GOOGLE_REFRESH_TOKEN", "") or os.environ.get("GOOGLE_REFRESH_TOKEN", ""))
    if not (cid and secret and token):
        bad("GOOGLE_CLIENT_ID / _SECRET / _REFRESH_TOKEN me se kuch missing hai")
        return 1
    good, message = test_credentials(cid, secret, token)
    (ok if good else bad)(message)
    if good:
        say("\n" + env_block({"GOOGLE_CLIENT_ID": cid, "GOOGLE_CLIENT_SECRET": secret,
                              "GOOGLE_REFRESH_TOKEN": token}, host))
    return 0 if good else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Guided setup for the Drive→Telegram bot")
    parser.add_argument("--check-only", action="store_true", help="validate .env only")
    parser.add_argument("--host", default="render", help="render | railway | heroku | local")
    parser.add_argument("--path", choices=["rclone", "own", "public"], help="skip the menu")
    args = parser.parse_args()

    say(f"{BOLD}Drive → Telegram bot · setup wizard{RESET}")
    say(f"{DIM}3 raaste hain. Sabse aasan: rclone (Cloud Console nahi chahiye).{RESET}")

    if args.check_only:
        return check_only(args.host)

    if args.path == "rclone":
        return flow_rclone(args.host)
    if args.path == "own":
        return flow_own_client(args.host)
    if args.path == "public":
        return flow_public(args.host)

    say("")
    say(f"  {BOLD}1{RESET}) rclone        — Cloud Console / Playground / redirect URI kuch nahi")
    say(f"  {BOLD}2{RESET}) apna client   — Cloud Console se (apna quota, 5 min)")
    say(f"  {BOLD}3{RESET}) public only   — koi setup nahi, sirf public links")
    say(f"  {BOLD}4{RESET}) sirf check    — .env me jo hai usko validate karo")

    choice = input("\nChoice [1/2/3/4]: ").strip() or "1"
    if choice == "1":
        return flow_rclone(args.host)
    if choice == "2":
        return flow_own_client(args.host)
    if choice == "3":
        return flow_public(args.host)
    if choice == "4":
        return check_only(args.host)
    bad("1-4 me se chuno")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
