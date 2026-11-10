"""
health.py — self-diagnosis the bot can run on itself, printed into Telegram.

Used by:
  * /doctor in the chat (so nobody needs a shell/Render dashboard)
  * a startup report to the admin, so "bot online but silent" never happens
    silently again
  * doctor.py (CLI) reuses the same checks

Every check returns (label, ok | None, detail). Tokens are masked, so the output
can be pasted in a chat without leaking credentials.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiohttp

TOKEN_URI = "https://oauth2.googleapis.com/token"
DRIVE_API = "https://www.googleapis.com/drive/v3"


@dataclass
class Check:
    label: str
    ok: Optional[bool]
    detail: str = ""

    @property
    def icon(self) -> str:
        return "✅" if self.ok is True else ("❌" if self.ok is False else "•")


def mask(value: str, keep: int = 6) -> str:
    if not value:
        return "(missing)"
    if len(value) <= keep * 2:
        return value[:2] + "…"
    return f"{value[:keep]}…{value[-4:]}"


def clean(value: str) -> str:
    return (value or "").strip().strip("'\"").strip()


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #
def check_config(cfg) -> list[Check]:
    out: list[Check] = []
    bot_token = clean(cfg.bot_token)
    out.append(
        Check(
            "BOT_TOKEN",
            bool(bot_token and ":" in bot_token),
            mask(bot_token) if bot_token else "missing",
        )
    )
    if not cfg.allowed_users:
        out.append(
            Check(
                "ALLOWED_USER_IDS",
                None,
                "empty — anyone who finds the bot can use your Google quota",
            )
        )
    else:
        out.append(Check("ALLOWED_USER_IDS", True, str(sorted(cfg.allowed_users))))
    out.append(
        Check(
            "folder order",
            True,
            f"{cfg.folder_order} · max {cfg.max_folder_files} files · "
            f"limit {cfg.max_download_gb} GB",
        )
    )
    return out


async def check_google(session: aiohttp.ClientSession, credentials, api_key) -> tuple[list[Check], Optional[str]]:
    """Verify the credential set and return an access token when possible."""
    out: list[Check] = []
    if credentials is None:
        if api_key:
            out.append(Check("Google", None, "API key only — public links work, folders don't"))
        else:
            out.append(
                Check(
                    "Google credentials",
                    False,
                    "none configured — only 'anyone with the link' files work",
                )
            )
        return out, None

    cid = clean(os.environ.get("GOOGLE_CLIENT_ID", ""))
    secret = clean(os.environ.get("GOOGLE_CLIENT_SECRET", ""))
    using_oauth = bool(cid and secret)

    try:
        access = await credentials.token()
    except Exception as exc:  # noqa: BLE001
        out.append(Check("Google token", False, str(exc).splitlines()[0][:160]))
        return out, None

    if using_oauth:
        wrong_client = False
        try:
            async with session.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"access_token": access},
            ) as r:
                info = await r.json() if r.status == 200 else {}
            aud = clean(info.get("aud") or info.get("azp") or "")
            if aud and aud != cid:
                wrong_client = True
                out.append(
                    Check(
                        "token ↔ client",
                        False,
                        f"token was issued to {mask(aud)} but GOOGLE_CLIENT_ID is "
                        f"{mask(cid)} — mint a new token with the same client",
                    )
                )
            elif aud:
                out.append(Check("token ↔ client", True, "match"))
            scope = clean(info.get("scope", ""))
            if scope:
                out.append(
                    Check(
                        "Drive scope",
                        "drive" in scope,
                        scope.replace("https://www.googleapis.com/auth/", ""),
                    )
                )
        except Exception as exc:  # noqa: BLE001
            out.append(Check("tokeninfo", None, repr(exc)[:120]))

        if not wrong_client:
            out.append(Check("Google token", True, "refresh works"))
    else:
        out.append(Check("Google token", True, "service account / api key"))

    if credentials is not None:
        try:
            who = await credentials.whoami() if hasattr(credentials, "whoami") else ""
        except Exception:  # noqa: BLE001
            who = ""
        mode = "service account" if getattr(credentials, "is_service_account", False) else "OAuth user"
        out.append(Check("account", True, f"{mask(who) if who else credentials.email} ({mode})"))
    return out, access


async def check_drive_folder(
    session: aiohttp.ClientSession, access: Optional[str], folder_id: str
) -> list[Check]:
    if not folder_id:
        return []
    if not access:
        return [Check("folder access", False, "no access token")]
    params = {
        "q": f"'{folder_id}' in parents and trashed = false",
        "fields": "files(name,mimeType),nextPageToken",
        "pageSize": "8",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
        "orderBy": "folder,name_natural",
    }
    try:
        async with session.get(
            f"{DRIVE_API}/files",
            params=params,
            headers={"Authorization": f"Bearer {access}"},
        ) as r:
            if r.status != 200:
                body = await r.text()
                reason = "not shared with this account" if r.status in (403, 404) else body[:120]
                return [Check("folder access", False, f"HTTP {r.status} — {reason}")]
            data = await r.json()
    except Exception as exc:  # noqa: BLE001
        return [Check("folder access", False, f"network: {repr(exc)[:100]}")]

    files = data.get("files", [])
    names = ", ".join((f.get("name") or "?")[:24] for f in files[:4])
    return [
        Check(
            "folder access",
            True,
            f"{len(files)} entries visible ({names}{'…' if files else ''})",
        )
    ]


async def check_telegram(bot) -> list[Check]:
    out: list[Check] = []
    try:
        me = await bot.get_me()
        out.append(Check("Telegram token", True, f"@{me.username} (id {me.id})"))
    except Exception as exc:  # noqa: BLE001
        out.append(Check("Telegram token", False, f"{type(exc).__name__}: {str(exc)[:120]}"))
        return out

    try:
        info = await bot.get_webhook_info()
        if info.url:
            out.append(
                Check(
                    "webhook",
                    False,
                    f"SET → {info.url[:60]} · polling will hit 409 Conflict. "
                    "Fix: /fixwebhook",
                )
            )
        else:
            pending = getattr(info, "pending_update_count", 0) or 0
            out.append(Check("webhook", True, f"not set (polling ok, {pending} pending)"))
    except Exception as exc:  # noqa: BLE001
        out.append(Check("webhook", None, repr(exc)[:100]))
    return out


def check_runtime(cfg) -> list[Check]:
    out: list[Check] = []
    temp = Path(cfg.temp_dir or tempfile.gettempdir())
    try:
        temp.mkdir(parents=True, exist_ok=True)
        probe = temp / ".health"
        probe.write_bytes(b"x")
        probe.unlink()
        out.append(Check("temp dir", True, str(temp)))
    except Exception as exc:  # noqa: BLE001
        out.append(Check("temp dir", False, f"{temp}: {exc!r}"))

    db = Path(cfg.jobs_db) if cfg.jobs_db else temp / "jobs.sqlite3"
    try:
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE IF NOT EXISTS _probe (x INTEGER)")
        conn.execute("DROP TABLE _probe")
        conn.commit()
        conn.close()
        out.append(Check("ledger", True, str(db)))
    except Exception as exc:  # noqa: BLE001
        out.append(
            Check(
                "ledger",
                False,
                f"{db}: {exc!r} — volume `unable to open database file` issue; "
                "check the /data mount",
            )
        )

    try:
        import shutil

        free = shutil.disk_usage(temp).free / 1024**3
        out.append(Check("disk free", free > 2, f"{free:.1f} GB"))
    except Exception:  # noqa: BLE001
        pass

    # ffmpeg/ffprobe: /merge and /streamdl cannot work without them, and the
    # failure mode is otherwise a confusing "track join failed" much later.
    try:
        import media_mux

        if media_mux.available():
            where = media_mux.FFMPEG or "?"
            probe_ok = bool(media_mux.FFPROBE)
            out.append(
                Check(
                    "ffmpeg",
                    True,
                    f"{where}" + ("" if probe_ok else " (no ffprobe — verification is best-effort)"),
                )
            )
        else:
            out.append(
                Check(
                    "ffmpeg",
                    False,
                    "missing — /merge and /streamdl disabled. Rebuild the Docker "
                    "image (it apt-installs ffmpeg) or `pip install imageio-ffmpeg`.",
                )
            )
    except Exception as exc:  # noqa: BLE001
        out.append(Check("ffmpeg", None, f"could not check: {exc!r}"))

    # Which features this deployment actually has switched on.
    scope = getattr(cfg, "drive_scope", "readonly")
    auto_fix = getattr(cfg, "auto_quota_fix", False)
    out.append(
        Check(
            "features",
            True,
            f"scope={scope} · auto quota fix={'on' if auto_fix else 'off'} · "
            f"upload cap={cfg.max_upload_mb} MB",
        )
    )
    return out


# --------------------------------------------------------------------------- #
# orchestration + rendering
# --------------------------------------------------------------------------- #
async def run_all(bot, cfg, client, folder_id: str = "") -> list[Check]:
    checks = list(check_config(cfg))
    checks += await check_telegram(bot)
    async with aiohttp.ClientSession() as session:
        google_checks, access = await check_google(session, client.credentials, client.api_key)
        checks += google_checks
        if folder_id:
            checks += await check_drive_folder(session, access, folder_id)
    checks += check_runtime(cfg)
    return checks


def render(checks: list[Check], title: str = "🩺 <b>Health check</b>") -> str:
    lines = [title, ""]
    for check in checks:
        lines.append(f"{check.icon} <b>{check.label}</b> — {check.detail}")
    failures = [c for c in checks if c.ok is False]
    lines.append("")
    if not failures:
        lines.append("✅ Sab theek hai. Folder link bhejo → ▶️ Send all.")
    else:
        lines.append(f"❌ {len(failures)} problem — pehla fix karo, baaki usi ke symptom hote hain.")
        first = failures[0]
        hint = _HINTS.get(first.label, "")
        if hint:
            lines.append(f"\n👉 <b>{first.label}</b>: {hint}")
    return "\n".join(lines)


_HINTS = {
    "BOT_TOKEN": "BotFather se token dobara lo, host ke env vars me daalo.",
    "Google credentials": "Credentials add karo, ya public-only mode me single links bhejo.",
    "Google token": "Token refresh fail — /doctor ka detail dekho; Client ID/secret token se match nahi hain.",
    "token ↔ client": "Playground me ⚙ → 'Use your own OAuth credentials' tick karke naya token banao.",
    "Drive scope": "Drive.readonly scope ke saath dobara authorize karo.",
    "folder access": "Folder bot ke Google account ko share nahi hai — Viewer permission do, ya account badlo.",
    "webhook": "Koi purana webhook set hai. Chat me /fixwebhook bhejo.",
    "ledger": "Volume writable nahi — disk mount /data pe hona chahiye.",
    "temp dir": "TEMP_DIR writable nahi — disk mount check karo.",
}


def compact(checks: list[Check]) -> str:
    """One-line-per-check version for the startup message."""
    bad = [c for c in checks if c.ok is False]
    total = len([c for c in checks if c.ok is not None])
    if not bad:
        return f"✅ Bot online — {total}/{total} checks pass"
    labels = ", ".join(c.label for c in bad[:3])
    return f"⚠️ Bot online, {len(bad)} issue: {labels}"
