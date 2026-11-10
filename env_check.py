"""
env_check.py — catch "deployed but silent" mistakes before they cost an hour.

Run this on your laptop before deploying, or on the host after deploying:

    python env_check.py            # uses the current environment
    python env_check.py --env .env # reads a key=value file instead

It validates the exact things that go wrong in practice on Render/Railway:

  * BOT_TOKEN with a smart-quote, a trailing space, or the word "BOT_TOKEN"
    copied along with the value  -> Telegram answers 401 and PTB just retries
  * ALLOWED_USER_IDS with a leading "@" (that's a username, not an id) -> the
    bot answers "This bot is private" to its own owner and looks broken
  * GOOGLE_REFRESH_TOKEN missing its client id/secret pair
  * MAX_UPLOAD_MB > 50 without a local Bot API server
  * MAX_UPLOAD_MB bigger than what the public API accepts
  * MAX_DOWNLOAD_GB bigger than the disk actually mounted
  * DATA_DIR/JOBS_DB on a path that will be wiped on redeploy
  * ffmpeg present (needed by /merge and /streamdl)

Exit code 0 = deploy it, 1 = fix something first.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

OK = "\033[92m✓\033[0m"
BAD = "\033[91m✗\033[0m"
WARN = "\033[93m!\033[0m"

_issues: list[str] = []
_warnings: list[str] = []


def ok(msg: str) -> None:
    print(f"{OK} {msg}")


def bad(msg: str) -> None:
    print(f"{BAD} {msg}")
    _issues.append(msg)


def warn(msg: str) -> None:
    print(f"{WARN} {msg}")
    _warnings.append(msg)


def load_env_file(path: Path) -> None:
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.split(" #")[0].strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def check_bot_token() -> None:
    token = get("BOT_TOKEN")
    if not token:
        bad("BOT_TOKEN is empty — get one from @BotFather")
        return
    if token != os.environ.get("BOT_TOKEN", ""):  # has surrounding whitespace
        warn("BOT_TOKEN had leading/trailing spaces — trimmed for this check; fix it in the dashboard too")
    if any(ord(ch) > 127 for ch in token):
        bad("BOT_TOKEN contains non-ASCII characters (usually a typographic dash or smart quote) — copy it again")
        return
    if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", token):
        if ":" not in token:
            bad("BOT_TOKEN has no ':' — that is not a bot token")
        else:
            warn("BOT_TOKEN does not look like <digits>:<35 chars>; double-check it")
        return
    ok(f"BOT_TOKEN looks valid ({token.split(':')[0]}:…)")


def check_users() -> None:
    raw = get("ALLOWED_USER_IDS")
    if not raw:
        warn("ALLOWED_USER_IDS is empty — anyone who finds the bot can burn your Drive quota")
        return
    ids = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    good = [i for i in ids if i.lstrip("-").isdigit()]
    if len(good) != len(ids):
        bad(
            "ALLOWED_USER_IDS must be numeric Telegram ids (from @userinfobot), "
            f"not usernames: {[i for i in ids if i not in good]}"
        )
        return
    ok(f"ALLOWED_USER_IDS = {', '.join(good)}")


def check_google() -> None:
    cid, secret = get("GOOGLE_CLIENT_ID"), get("GOOGLE_CLIENT_SECRET")
    refresh = get("GOOGLE_REFRESH_TOKEN")
    sa_json = get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sa_file = get("GOOGLE_SERVICE_ACCOUNT_FILE")
    api_key = get("GOOGLE_API_KEY")

    if refresh:
        if not (cid and secret):
            bad("GOOGLE_REFRESH_TOKEN is set but GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are missing")
        elif not refresh.startswith("1//"):
            warn("GOOGLE_REFRESH_TOKEN normally starts with '1//' — check you pasted the refresh token, not the access token")
        else:
            ok("OAuth credentials complete (client id + secret + refresh token)")
    if sa_json:
        if not sa_json.lstrip().startswith("{"):
            bad("GOOGLE_SERVICE_ACCOUNT_JSON must be the JSON itself (starts with '{'), not a filename")
        elif '"private_key"' not in sa_json:
            bad("GOOGLE_SERVICE_ACCOUNT_JSON has no private_key — paste the whole key file")
        elif "\n" in sa_json.strip() and not sa_json.strip().startswith('{"'):
            warn("GOOGLE_SERVICE_ACCOUNT_JSON contains raw newlines; most dashboards keep them, but a single line is safer")
        else:
            ok("Service-account JSON present")
    if sa_file and not Path(sa_file).is_file():
        bad(f"GOOGLE_SERVICE_ACCOUNT_FILE points at {sa_file}, which does not exist on this host")
    if not (refresh or sa_json or sa_file):
        if api_key:
            warn("Only GOOGLE_API_KEY set: public files work, private files will not")
        else:
            warn("No Google credentials: public 'anyone with the link' files only")


def check_uploads() -> None:
    try:
        max_mb = int(get("MAX_UPLOAD_MB") or 2000)
    except ValueError:
        bad("MAX_UPLOAD_MB is not a number")
        return
    local_api = bool(get("BOT_API_BASE_URL"))
    if max_mb > 2000:
        bad("MAX_UPLOAD_MB above 2000 is impossible even with a local Bot API server")
    elif max_mb > 50 and not local_api:
        warn(
            f"MAX_UPLOAD_MB={max_mb} with no BOT_API_BASE_URL: Telegram documents a "
            "50 MB ceiling for bots. Big files often pass, but not reliably — the "
            "bot splits anything over the limit into parts."
        )
    else:
        ok(f"Upload ceiling {max_mb} MB ({'local Bot API' if local_api else 'public API'})")
    try:
        timeout = int(get("MEDIA_WRITE_TIMEOUT") or 1800)
        if timeout < 300:
            warn(f"MEDIA_WRITE_TIMEOUT={timeout}s is low; big videos will die mid-upload (use 1800)")
        else:
            ok(f"MEDIA_WRITE_TIMEOUT = {timeout}s")
    except ValueError:
        bad("MEDIA_WRITE_TIMEOUT is not a number")


def check_disk() -> None:
    try:
        gb = float(get("MAX_DOWNLOAD_GB") or 20)
    except ValueError:
        bad("MAX_DOWNLOAD_GB is not a number")
        return
    data_dir = get("DATA_DIR") or "/data"
    path = Path(data_dir)
    exists = path.exists()
    if not exists:
        warn(f"DATA_DIR {data_dir} does not exist on this machine (normal before a deploy)")
    else:
        try:
            free = shutil.disk_usage(path).free / 1024**3
            if free < gb:
                bad(f"Only {free:.1f} GB free under {data_dir} but MAX_DOWNLOAD_GB={gb}")
            else:
                ok(f"Disk under {data_dir}: {free:.1f} GB free for a {gb} GB cap")
        except OSError as exc:
            warn(f"could not stat {data_dir}: {exc}")
    jobs_db = get("JOBS_DB") or ""
    if jobs_db and not jobs_db.startswith(data_dir) and data_dir != "/data":
        warn(f"JOBS_DB={jobs_db} is outside DATA_DIR — the resume ledger will be wiped on redeploy")
    elif jobs_db.startswith("/data") or jobs_db.startswith(data_dir):
        ok(f"Resume ledger inside the persistent disk ({jobs_db})")
    else:
        warn("JOBS_DB not set to a path on the persistent disk — courses may restart from file 1")


def check_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool):
            ok(f"{tool} found ({shutil.which(tool)})")
        else:
            warn(f"{tool} not on PATH here — needed only for /merge and /streamdl (the Docker image installs it)")


def check_misc() -> None:
    scope = (get("DRIVE_SCOPE") or "readonly").lower()
    if scope in ("full", "drive"):
        ok("DRIVE_SCOPE=full (/quotafix and /streamdl copies enabled)")
    else:
        ok("DRIVE_SCOPE=readonly (safest; /quotafix needs full)")
    order = (get("FOLDER_ORDER") or "deep").lower()
    if order not in ("deep", "breadth"):
        bad(f"FOLDER_ORDER={order} — use 'deep' (Module 1 complete, then Module 2) or 'breadth'")
    else:
        ok(f"FOLDER_ORDER={order}")
    if get("HEALTHCHECK_ENABLED").lower() in ("1", "true", "yes", "on"):
        ok("HEALTHCHECK_ENABLED on (serves $PORT — good on Cloud Run/Render web services)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", help="read a .env-style file instead of the process environment")
    args = ap.parse_args()
    if args.env:
        path = Path(args.env)
        if not path.is_file():
            sys.exit(f"✗ {path} not found")
        load_env_file(path)
        print(f"Reading {path}\n")

    check_bot_token()
    check_users()
    check_google()
    check_uploads()
    check_disk()
    check_tools()
    check_misc()

    print()
    if _issues:
        print(f"{BAD} {len(_issues)} problem(s) to fix before deploying:")
        for item in _issues:
            print(f"   • {item}")
        return 1
    if _warnings:
        print(f"{WARN} {len(_warnings)} warning(s) — deployable, but read them:")
        for item in _warnings:
            print(f"   • {item}")
    else:
        print(f"{OK} Everything checks out.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
