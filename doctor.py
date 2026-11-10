"""
doctor.py — one command, full health check, copy-paste-able report.

    python doctor.py                     # checks .env, Google, Telegram, disk
    python doctor.py --folder <FOLDER_ID>  # also prove Drive access to a folder

Nothing secret is printed: tokens are masked. Safe to paste the output back.

It checks, in order:
  1. Python + dependencies
  2. .env presence and required keys (format, quotes, placeholders)
  3. Google refresh-token exchange + Drive scope + which account
  4. Drive folder access (optional)
  5. Telegram getMe  -> is the bot token valid?
  6. Telegram webhook -> THE classic silent killer: a registered webhook makes
     polling do nothing (409 Conflict) and the bot looks dead
  7. pending updates / another poller running
  8. disk + sqlite ledger writability
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RESULTS: list[tuple[str, bool | None, str]] = []  # (name, ok/None/False, detail)


def add(name: str, ok: bool | None, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    icon = "✓" if ok is True else ("✗" if ok is False else "•")
    print(f"  [{icon}] {name}" + (f" — {detail}" if detail else ""))


def head(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 58 - len(title)))


def mask(value: str, keep: int = 6) -> str:
    if not value:
        return "(missing)"
    if len(value) <= keep * 2:
        return value[:2] + "…"
    return f"{value[:keep]}…{value[-4:]} (len {len(value)})"


def load_env() -> dict[str, str]:
    path = Path(".env")
    data: dict[str, str] = {}
    if path.is_file():
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            data[key.strip()] = value.strip()
    return data


def env(name: str, file_env: dict[str, str]) -> str:
    return (os.environ.get(name) or file_env.get(name) or "").strip()


def clean(value: str) -> str:
    return value.strip().strip("'\"").strip()


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #
def check_python() -> None:
    head("1. Python / dependencies")
    add("Python", True, sys.version.split()[0])
    for module in ("telegram", "aiohttp", "google.auth"):
        try:
            __import__(module)
            version = getattr(__import__(module), "__version__", "")
            add(module, True, version)
        except Exception as exc:  # noqa: BLE001
            add(module, False, f"not importable: {exc!r} — run: pip install -r requirements.txt")


def check_env(file_env: dict[str, str]) -> dict[str, str]:
    head("2. Configuration")
    values = {}
    required = ["BOT_TOKEN", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
                "GOOGLE_REFRESH_TOKEN", "ALLOWED_USER_IDS"]
    for name in required:
        raw = env(name, file_env)
        values[name] = clean(raw)
        source = "env" if os.environ.get(name) else (".env" if file_env.get(name) else "missing")
        if not raw:
            add(name, False, "MISSING")
            continue
        notes = []
        if raw != raw.strip():
            notes.append("whitespace")
        if raw.startswith(("'", '"')):
            notes.append("quotes included — remove them")
        if raw.lower().startswith(("your_", "xxx", "...")):
            notes.append("placeholder")
        if name == "BOT_TOKEN" and ":" not in raw:
            notes.append("malformed (expect 123456:ABC…)")
        if name == "GOOGLE_CLIENT_ID" and not raw.rstrip('"\'').endswith(
            ".apps.googleusercontent.com"
        ):
            notes.append("does not end with .apps.googleusercontent.com")
        if name == "GOOGLE_CLIENT_SECRET" and not raw.lstrip('"\'').startswith("GOCSPX-"):
            notes.append("does not start with GOCSPX-")
        if name == "GOOGLE_REFRESH_TOKEN" and not raw.lstrip('"\'').startswith("1//"):
            notes.append("does not start with 1// (access token pasted?)")
        add(name, not notes, f"{mask(raw)} from {source}" + (f" · {'; '.join(notes)}" if notes else ""))
    return values


def check_google(values: dict[str, str]) -> str | None:
    head("3. Google credentials")
    cid = values.get("GOOGLE_CLIENT_ID", "")
    secret = values.get("GOOGLE_CLIENT_SECRET", "")
    token = values.get("GOOGLE_REFRESH_TOKEN", "")
    if not (cid and secret and token):
        add("token exchange", False, "skipped — values missing/invalid above")
        return None

    body = urllib.parse.urlencode({
        "client_id": cid, "client_secret": secret,
        "refresh_token": token, "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            err = json.loads(raw).get("error", f"HTTP {exc.code}")
            desc = json.loads(raw).get("error_description", "")
        except ValueError:
            err, desc = f"HTTP {exc.code}", raw[:120]
        hint = {
            "invalid_client": "client id/secret ≠ the client that made the token",
            "invalid_grant": "token revoked/expired (Testing mode = 7 days)",
            "unauthorized_client": "make the client type 'Desktop app'",
        }.get(err, "")
        add("token exchange", False, f"{err}: {desc}" + (f" · {hint}" if hint else ""))
        return None
    except Exception as exc:  # noqa: BLE001
        add("token exchange", False, f"network: {exc!r}")
        return None

    access = payload.get("access_token", "")
    add("token exchange", True, "refresh works")

    scopes = payload.get("scope", "")
    info: dict = {}
    if access:
        try:
            with urllib.request.urlopen(
                "https://oauth2.googleapis.com/tokeninfo?"
                + urllib.parse.urlencode({"access_token": access}), timeout=20
            ) as resp:
                info = json.loads(resp.read().decode())
            scopes = info.get("scope", scopes)
        except Exception:  # noqa: BLE001
            pass
    has_drive = "drive" in (scopes or "")
    add("Drive scope", has_drive, scopes or "unknown")
    if info.get("aud"):
        add("token issued to", info["aud"] == cid, f"client {info['aud']} · configured {cid}")
    if info.get("email"):
        add("account", True, info["email"])
    return access


def check_folder(access: str | None, folder_id: str) -> None:
    head("4. Drive folder access")
    if not access:
        add("folder", False, "skipped — no access token")
        return
    query = urllib.parse.urlencode({
        "q": f"'{folder_id}' in parents and trashed = false",
        "fields": "files(id,name,mimeType) ,nextPageToken",
        "pageSize": "8",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
        "orderBy": "folder,name_natural",
    })
    req = urllib.request.Request(
        f"https://www.googleapis.com/drive/v3/files?{query}",
        headers={"Authorization": f"Bearer {access}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        add("folder", False, f"HTTP {exc.code} — not shared with this account, or wrong id")
        return
    except Exception as exc:  # noqa: BLE001
        add("folder", False, f"network: {exc!r}")
        return
    files = data.get("files", [])
    add("folder", bool(files), f"{len(files)} entries: " + ", ".join(
        f.get("name", "?")[:28] for f in files[:4]))


def check_telegram(values: dict[str, str]) -> str | None:
    head("5. Telegram")
    token = values.get("BOT_TOKEN", "")
    if not token:
        add("getMe", False, "no BOT_TOKEN")
        return None

    def tg(method: str, **params) -> tuple[int, dict]:
        url = f"https://api.telegram.org/bot{token}/{method}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode())
            except ValueError:
                return exc.code, {}

    try:
        status, data = tg("getMe")
    except Exception as exc:  # noqa: BLE001
        add("getMe", False, f"network: {exc!r} — Render se Telegram reachable hai?")
        return None
    if not data.get("ok"):
        add("getMe", False, f"{data.get('description', status)} — token invalid/revoked")
        return None
    me = data["result"]
    add("getMe", True, f"@{me.get('username')} (id {me.get('id')})")

    status, hook = tg("getWebhookInfo")
    if hook.get("ok"):
        url = hook["result"].get("url") or ""
        if url:
            add("webhook", False, f"webhook SET → {url} — polling will get 409 Conflict "
                                   "and the bot looks dead. Run: "
                                   f"curl https://api.telegram.org/bot<TOKEN>/deleteWebhook")
        else:
            add("webhook", True, "not set (polling can work)")
    return me.get("username")


def check_runtime(file_env: dict[str, str]) -> None:
    head("6. Runtime / disk / ledger")
    temp = Path(env("TEMP_DIR", file_env) or tempfile.gettempdir())
    try:
        temp.mkdir(parents=True, exist_ok=True)
        probe = temp / ".doctor"
        probe.write_bytes(b"x" * 1024)
        probe.unlink()
        add("temp dir writable", True, str(temp))
    except Exception as exc:  # noqa: BLE001
        add("temp dir writable", False, f"{temp}: {exc!r}")

    try:
        import shutil

        usage = shutil.disk_usage(temp)
        free_gb = usage.free / 1024**3
        add("free disk", free_gb > 2, f"{free_gb:.1f} GB")
    except Exception:  # noqa: BLE001
        pass

    db = Path(env("JOBS_DB", file_env) or Path(temp) / "jobs.sqlite3")
    try:
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE IF NOT EXISTS _probe (x INTEGER)")
        conn.execute("DROP TABLE _probe")
        conn.commit()
        conn.close()
        add("ledger writable", True, str(db))
    except Exception as exc:  # noqa: BLE001
        add("ledger writable", False, f"{db}: {exc!r} (volume ownership?)")


def verdict() -> int:
    head("VERDICT")
    failures = [r for r in RESULTS if r[1] is False]
    if not failures:
        print("  Everything checks out.")
        print("  Next: Telegram pe /start → /auth → folder link → ▶️ Send all.")
        print("  If the bot still does not reply, it is not running: check the")
        print("  host logs for the worker process (Render: Deploys → Logs).")
        return 0
    print(f"  {len(failures)} problem(s):")
    for name, _, detail in failures:
        print(f"   • {name}: {detail}")
    print("\n  Fix the first one; later ones are often symptoms of it.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Bot health check")
    parser.add_argument("--folder", default="", help="Drive folder id to verify access")
    args = parser.parse_args()

    print(f"doctor.py · {time.strftime('%Y-%m-%d %H:%M:%S')}")
    file_env = load_env()
    print(f"working dir: {Path.cwd()} · .env {'found' if file_env else 'NOT found'}")

    check_python()
    values = check_env(file_env)
    access = check_google(values)
    if args.folder:
        check_folder(access, args.folder.split("/folders/")[-1].split("?")[0])
    check_telegram(values)
    check_runtime(file_env)
    return verdict()


if __name__ == "__main__":
    raise SystemExit(main())
