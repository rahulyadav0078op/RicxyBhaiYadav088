"""
selftest.py — one command that proves the whole bot is healthy on the host.

    python selftest.py

Run it in the Render/Railway shell (or any VPS) right after deploying. It never
touches Telegram's API with a message, and it never downloads a real course
file, so it is safe to run while the bot is live. It answers, in plain
language, the questions that actually decide whether the bot will work:

  1. Environment    — token shape, user ids, Google credentials present
  2. Disk           — /data writable, ledger SQLite opens, free space vs cap
  3. ffmpeg         — binary + codecs needed by /merge and /streamdl
  4. Modules        — every file the bot imports resolves
  5. Pipeline       — build a video track + an audio track, mux them, verify
                      the result has both streams (the real /streamdl core)
  6. Telegram       — getMe (bot token valid, username, whether it can be added)
  7. Google         — mint an access token and read Drive about-info
  8. Stream scrape  — fetch a Drive preview page and confirm the DASH split
                      (skipped when no public sample link is given)

Exit code 0 = all green, 1 = something failed (each failure prints the fix).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

PASS = "✅"
FAIL = "❌"
INFO = "•"

_results: list[tuple[str, bool | None, str]] = []


def record(label: str, ok: bool | None, detail: str = "") -> None:
    icon = PASS if ok is True else (FAIL if ok is False else INFO)
    print(f"{icon} {label}" + (f" — {detail}" if detail else ""))
    _results.append((label, ok, detail))


# --------------------------------------------------------------------------- #
# 1. environment
# --------------------------------------------------------------------------- #
def check_env() -> None:
    from env_check import (  # reuse the same rules as the pre-deploy check
        check_bot_token,
        check_google,
        check_uploads,
        check_users,
    )

    import env_check

    before = len(env_check._issues)
    check_bot_token()
    check_users()
    check_google()
    check_uploads()
    new_issues = env_check._issues[before:]
    record("environment", not new_issues, "; ".join(new_issues) if new_issues else "no blocking problems")


# --------------------------------------------------------------------------- #
# 2. disk
# --------------------------------------------------------------------------- #
def check_disk() -> None:
    from bot import BotConfig

    cfg = BotConfig.from_env()
    data_dir = Path(cfg.temp_dir or tempfile.gettempdir())
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".selftest"
        probe.write_bytes(b"x")
        probe.unlink()
        record("temp dir writable", True, str(data_dir))
    except Exception as exc:  # noqa: BLE001
        record("temp dir writable", False, f"{data_dir}: {exc!r}")

    db = Path(cfg.jobs_db) if cfg.jobs_db else data_dir / "jobs.sqlite3"
    try:
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE IF NOT EXISTS _selftest (x INTEGER)")
        conn.execute("DROP TABLE _selftest")
        conn.commit()
        conn.close()
        record("ledger DB", True, str(db))
    except Exception as exc:  # noqa: BLE001
        record("ledger DB", False, f"{db}: {exc!r} (check the /data volume mount)")

    try:
        free = shutil.disk_usage(data_dir).free / 1024**3
        # /streamdl and /merge hold the video track + the audio track + the
        # joined file at the same time, so the real requirement is ~1.5x the
        # per-file cap, plus a little slack.
        need = cfg.max_download_gb * 1.5
        ok = free >= need
        record(
            "free disk",
            ok,
            f"{free:.1f} GB free, MAX_DOWNLOAD_GB={cfg.max_download_gb} needs ~{need:.1f} GB"
            + ("" if ok else " — raise the disk size or lower MAX_DOWNLOAD_GB"),
        )
    except Exception as exc:  # noqa: BLE001
        record("free disk", None, str(exc))


# --------------------------------------------------------------------------- #
# 3. ffmpeg
# --------------------------------------------------------------------------- #
def check_ffmpeg() -> None:
    import media_mux

    if not media_mux.available():
        record(
            "ffmpeg",
            False,
            "missing — /merge and /streamdl cannot join tracks. "
            "Docker: rebuild the image. Elsewhere: apt-get install -y ffmpeg "
            "or pip install imageio-ffmpeg",
        )
        return
    record("ffmpeg", True, media_mux.FFMPEG or "?")
    proc = subprocess.run(
        [media_mux.FFMPEG, "-hide_banner", "-muxers"], capture_output=True, text=True, timeout=60
    )
    has_mp4 = "mp4" in proc.stdout
    record("mp4 muxer", has_mp4, "present" if has_mp4 else "missing — track joining will fail")
    record(
        "ffprobe",
        True if media_mux.FFPROBE else None,
        media_mux.FFPROBE or "absent — post-merge verification falls back to parsing ffmpeg output",
    )


# --------------------------------------------------------------------------- #
# 4. modules
# --------------------------------------------------------------------------- #
def check_modules() -> None:
    names = [
        "bot",
        "gdrive_core",
        "health",
        "healthcheck",
        "media_mux",
        "media_download",
        "railway_setup",
        "oauth_helper",
        "env_check",
        "doctor",
        "setup_wizard",
        "diagnose_credentials",
        "import_rclone_token",
    ]
    missing = []
    for name in names:
        try:
            __import__(name)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{name} ({type(exc).__name__})")
    record("modules import", not missing, ", ".join(missing) or f"{len(names)} modules OK")


# --------------------------------------------------------------------------- #
# 5. the real pipeline: two tracks -> mux -> verify
# --------------------------------------------------------------------------- #
def check_pipeline() -> None:
    """Synthesise a video-only and an audio-only file, join, verify.

    This is the same code path /streamdl uses, minus Google. If this passes, the
    only remaining variable in production is the network.
    """
    import media_mux

    if not media_mux.available():
        record("mux pipeline", None, "skipped (no ffmpeg)")
        return

    work = Path(tempfile.mkdtemp(prefix="selftest-mux-"))
    ff = media_mux.FFMPEG
    try:
        src = work / "src.mp4"
        subprocess.run(
            [ff, "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc=size=160x120:rate=15:duration=2",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)],
            check=True, capture_output=True, timeout=180,
        )
        video, audio = work / "video.mp4", work / "audio.m4a"
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(src), "-map", "0:v:0", "-c", "copy", str(video)], check=True, timeout=120)
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(src), "-map", "0:a:0", "-c", "copy", str(audio)], check=True, timeout=120)

        result = asyncio.run(media_mux.mux(video, audio, out=work / "final.mp4"))
        ok, detail = asyncio.run(media_mux.verify(result))
        record("mux pipeline", ok, detail)
        lossless = result.reencoded_audio is False
        record("stream copy (no re-encode)", lossless, "video+audio copied byte-for-byte" if lossless else "audio was re-encoded")
    except Exception as exc:  # noqa: BLE001
        record("mux pipeline", False, f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 6. telegram
# --------------------------------------------------------------------------- #
async def _telegram() -> None:
    import aiohttp

    from bot import BotConfig

    cfg = BotConfig.from_env()
    token = (cfg.bot_token or "").strip()
    if not token:
        record("telegram getMe", False, "BOT_TOKEN missing")
        return
    url = f"https://api.telegram.org/bot{token}/getMe"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as r:
            data = await r.json(content_type=None)
    if not data.get("ok"):
        record("telegram getMe", False, f"{data.get('description')} — token invalid or revoked")
        return
    me = data["result"]
    record("telegram getMe", True, f"@{me.get('username')} (id {me.get('id')})")

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=30
        ) as r:
            hook = await r.json(content_type=None)
    pending = hook.get("result", {}).get("pending_update_count", 0)
    url_set = hook.get("result", {}).get("url") or ""
    if url_set:
        record("webhook", False, f"a webhook is registered ({url_set}) — polling will 409. Send /fixwebhook")
    else:
        record("webhook", True, f"none registered (polling mode OK), {pending} pending update(s)")


# --------------------------------------------------------------------------- #
# 7. google
# --------------------------------------------------------------------------- #
async def _google() -> None:
    from bot import BotConfig, BotState

    cfg = BotConfig.from_env()
    state = BotState.__new__(BotState)  # build the client without the rest
    state.cfg = cfg
    client = BotState._build_client(state)
    try:
        if not client.authorized and not client.api_key:
            record("google", None, "no credentials — public files only (fine if that is all you need)")
            return
        try:
            who = await client.whoami()
            record("google identity", True, str(who))
        except Exception as exc:  # noqa: BLE001
            record("google identity", False, f"{exc} — see /doctor in the chat")
        # a real API round-trip: 1-byte field request, no download
        sess = await client.session()
        async with sess.get(
            "https://www.googleapis.com/drive/v3/about",
            params={"fields": "user,storageQuota"},
            headers=await client._auth_headers() if client.authorized else {},
        ) as r:
            record("drive API", r.status == 200, f"HTTP {r.status}")
    finally:
        await client.close()


# --------------------------------------------------------------------------- #
# 8. stream scrape (optional)
# --------------------------------------------------------------------------- #
async def _stream(sample: str) -> None:
    from gdrive_core import DriveClient, parse_drive_links

    refs = parse_drive_links(sample)
    if not refs:
        record("stream scrape", None, "no valid Drive link given")
        return
    client = DriveClient()
    try:
        video_url, audio_url = await client.get_stream_parts(refs[0].file_id)
    finally:
        await client.close()
    if video_url and audio_url:
        record("DASH split found", True, "video track + audio track URLs extracted")
        from media_download import clean_stream_url

        record(
            "range strip",
            "&range=" not in clean_stream_url(video_url),
            "`&range=` tail removed (tutorial step 7)",
        )
    elif video_url:
        record("DASH split found", True, "single video URL (already contains audio)")
    else:
        record(
            "DASH split found",
            False,
            "no stream URLs — the file is private (use /auth), not a video, or DRM-protected",
        )


def main() -> int:
    print("=" * 68)
    print("  Google Drive -> Telegram bot · self test")
    print("=" * 68)
    check_env()
    check_disk()
    check_ffmpeg()
    check_modules()
    check_pipeline()
    asyncio.run(_telegram())
    asyncio.run(_google())

    sample = os.getenv("SELFTEST_STREAM_LINK", "").strip()
    if sample:
        asyncio.run(_stream(sample))
    else:
        record("DASH split found", None, "skipped — set SELFTEST_STREAM_LINK=<public video link> to test")

    failed = [r for r in _results if r[1] is False]
    print("-" * 68)
    if failed:
        print(f"{FAIL} {len(failed)} check(s) failed:")
        for label, _ok, detail in failed:
            print(f"   • {label}: {detail}")
        return 1
    print(f"{PASS} all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
