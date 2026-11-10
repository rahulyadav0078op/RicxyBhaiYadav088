"""
bot.py — Telegram bot that fetches Google Drive files (public links, or
anything your own Google account can access) and re-uploads them to Telegram
without re-encoding, so the quality is bit-for-bit the original.

Run:
    cp .env.example .env      # fill in BOT_TOKEN (+ optional Google creds)
    python bot.py

Check config without connecting to Telegram:
    python bot.py --check
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import health as health_mod
import media_download
import media_mux
from gdrive_core import (
    CancelledByUser,
    DriveClient,
    DriveCredentials,
    DriveError,
    DriveFile,
    FileTooLargeError,
    GoogleAccessError,
    flatten_rel_path,
    format_gb,
    DRIVE_SCOPE_FULL,
    DRIVE_SCOPE_READONLY,
    QuotaExceededError,
    human_bytes,
    human_rate,
    iter_file_parts,
    progress_bar,
    eta_text,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("gdrive-bot")

TELEGRAM_HARD_LIMIT = 2000 * 1024 * 1024  # ceiling with a local Bot API server
# PTB defaults media uploads to a 20 s socket write timeout, which kills big
# videos. build_application() raises it; recorded here for tests.
MEDIA_WRITE_TIMEOUT_CONFIGURED = 0.0


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


@dataclass
class BotConfig:
    bot_token: str = ""
    allowed_users: set[int] = field(default_factory=set)
    api_key: Optional[str] = None
    service_account_file: Optional[str] = None
    refresh_token: Optional[str] = None
    delegated_subject: Optional[str] = None
    acknowledge_abuse: bool = False
    max_download_gb: float = 20.0
    max_upload_mb: int = 2000
    concurrent_downloads: int = 2
    temp_dir: str = ""
    files_ttl_seconds: int = 3600
    bot_api_base_url: Optional[str] = None
    keep_files: bool = False
    max_folder_files: int = 300
    folder_recursive: bool = True
    folder_order: str = "deep"
    jobs_db: Optional[str] = None
    service_account_json: Optional[str] = None
    drive_scope: str = "readonly"
    auto_quota_fix: bool = False

    @classmethod
    def from_env(cls) -> "BotConfig":
        def _ids(raw: str) -> set[int]:
            out: set[int] = set()
            for chunk in raw.replace(";", ",").split(","):
                chunk = chunk.strip()
                if chunk.lstrip("-").isdigit():
                    out.add(int(chunk))
            return out

        cfg = cls(
            bot_token=os.getenv("BOT_TOKEN", "").strip(),
            allowed_users=_ids(os.getenv("ALLOWED_USER_IDS", "")),
            api_key=(os.getenv("GOOGLE_API_KEY") or "").strip() or None,
            service_account_file=(
                os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or ""
            ).strip()
            or None,
            refresh_token=(os.getenv("GOOGLE_REFRESH_TOKEN") or "").strip() or None,
            delegated_subject=(os.getenv("GOOGLE_DELEGATED_SUBJECT") or "").strip()
            or None,
            acknowledge_abuse=_env_bool("DRIVE_ACKNOWLEDGE_ABUSE"),
            max_download_gb=float(os.getenv("MAX_DOWNLOAD_GB", "20") or 20),
            max_upload_mb=_env_int("MAX_UPLOAD_MB", 2000),
            concurrent_downloads=max(1, _env_int("CONCURRENT_DOWNLOADS", 2)),
            temp_dir=os.getenv("TEMP_DIR", "").strip(),
            files_ttl_seconds=_env_int("FILE_TTL_SECONDS", 3600),
            bot_api_base_url=(os.getenv("BOT_API_BASE_URL") or "").strip() or None,
            keep_files=_env_bool("KEEP_DOWNLOADED_FILES"),
            max_folder_files=_env_int("MAX_FOLDER_FILES", 300),
            folder_recursive=_env_bool("FOLDER_RECURSIVE", True),
            jobs_db=(os.getenv("JOBS_DB") or "").strip() or None,
            folder_order=(os.getenv("FOLDER_ORDER", "deep").strip().lower() or "deep"),
            service_account_json=(
                os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or ""
            ).strip()
            or None,
            drive_scope=(os.getenv("DRIVE_SCOPE", "readonly").strip().lower() or "readonly"),
            auto_quota_fix=_env_bool("AUTO_QUOTA_FIX"),
        )
        if cfg.folder_order not in {"deep", "breadth"}:
            log.warning("FOLDER_ORDER=%r unknown, using 'deep'", cfg.folder_order)
            cfg.folder_order = "deep"
        if cfg.max_upload_mb > 2000 and not cfg.bot_api_base_url:
            log.warning(
                "MAX_UPLOAD_MB=%s set, but no BOT_API_BASE_URL: Telegram's documented "
                "bot upload limit on the public API is 50 MB (many files pass far "
                "above it, but it is not guaranteed). For reliable large uploads run a "
                "local Bot API server and set BOT_API_BASE_URL + MAX_UPLOAD_MB=2000.",
                cfg.max_upload_mb,
            )
        return cfg


# --------------------------------------------------------------------------- #
# Runtime state
# --------------------------------------------------------------------------- #
@dataclass
class Job:
    chat_id: int
    user_id: int
    file: DriveFile
    path: Optional[Path] = None
    cancel: asyncio.Event = field(default_factory=asyncio.Event)


class BotState:
    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg
        self.downloads = asyncio.Semaphore(cfg.concurrent_downloads)
        self.active: dict[int, Job] = {}  # user_id -> newest job
        self.temp_root = Path(cfg.temp_dir or tempfile.gettempdir()) / "gdrive-bot"
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.started_at = time.time()
        self.client = self._build_client()
        self.store = JobStore(cfg.jobs_db)
        self.jobs = Jobs(cfg, self.store)

    def _build_client(self) -> DriveClient:
        cfg = self.cfg
        creds = None
        inline_info = None
        raw = cfg.service_account_json
        if raw:
            if raw.lstrip().startswith("{"):
                import json as _json

                try:
                    inline_info = _json.loads(raw)
                except ValueError as exc:
                    log.error("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON: %s", exc)
            else:
                # a path was supplied in the JSON variable — treat it as a file
                cfg.service_account_file = raw
        if inline_info or cfg.service_account_file or cfg.refresh_token:
            try:
                creds = DriveCredentials(
                    service_account_file=cfg.service_account_file,
                    service_account_info=inline_info,
                    refresh_token=cfg.refresh_token,
                    subject=cfg.delegated_subject,
                    scope=(
                        DRIVE_SCOPE_FULL
                        if cfg.drive_scope in ("full", "drive")
                        else DRIVE_SCOPE_READONLY
                    ),
                )
                log.info("Google credentials loaded (authorized mode).")
            except DriveError as exc:
                log.error("Could not load Google credentials: %s", exc)
        client = DriveClient(
            api_key=cfg.api_key,
            credentials=creds,
            acknowledge_abuse=cfg.acknowledge_abuse,
            on_log=log.debug,
        )
        if not creds and not cfg.api_key:
            log.warning(
                "No Google credentials or API key configured: only public "
                "'anyone with the link' files will work."
            )
        return client

    def new_job(self, chat_id: int, user_id: int, file: DriveFile) -> Job:
        job = Job(chat_id=chat_id, user_id=user_id, file=file)
        self.active[user_id] = job
        return job

    def clear(self, user_id: int, job: Job) -> None:
        if self.active.get(user_id) is job:
            self.active.pop(user_id, None)


class JobStore:
    """SQLite ledger so a crash mid-course-download doesn't lose your place.

    Completed file ids are recorded per chat; re-sending the same folder link
    resumes where it stopped instead of re-uploading hundreds of videos.
    """

    def __init__(self, path: Optional[str]) -> None:
        self.path = path or ":memory:"
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS done ("
            " chat_id INTEGER, file_id TEXT, name TEXT, size INTEGER,"
            " ts INTEGER, PRIMARY KEY (chat_id, file_id))"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cursors ("
            " chat_id INTEGER, folder_id TEXT, last_idx INTEGER, ts INTEGER,"
            " PRIMARY KEY (chat_id, folder_id))"
        )
        self._conn.commit()

    def is_done(self, chat_id: int, file_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM done WHERE chat_id=? AND file_id=?", (chat_id, file_id)
            )
            return cur.fetchone() is not None

    def mark_done(self, chat_id: int, file_id: str, name: str, size: Optional[int]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO done VALUES (?,?,?,?,?)",
                (chat_id, file_id, name, size or 0, int(time.time())),
            )
            self._conn.commit()

    def count_done(self, chat_id: int) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM done WHERE chat_id=?", (chat_id,))
            return int(cur.fetchone()[0])

    def reset(self, chat_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM done WHERE chat_id=?", (chat_id,))
            self._conn.execute("DELETE FROM cursors WHERE chat_id=?", (chat_id,))
            self._conn.commit()


class CourseJob:
    """A queued folder transfer, processed by one worker at a time."""

    def __init__(self, chat_id: int, user_id: int, folder_id: str, folder_name: str = ""):
        self.chat_id = chat_id
        self.user_id = user_id
        self.folder_id = folder_id
        self.folder_name = folder_name
        self.files: list[DriveFile] = []
        self.index = 0
        self.sent = 0
        self.skipped = 0
        self.failed = 0
        self.cancel = asyncio.Event()
        self.started = time.time()

    @property
    def total(self) -> int:
        return len(self.files)

    @property
    def current(self) -> Optional[DriveFile]:
        return self.files[self.index] if self.index < len(self.files) else None


class Jobs:
    """Single-consumer queue: one course transfer at a time, cancel-aware."""

    def __init__(self, cfg: "BotConfig", store: JobStore) -> None:
        self.cfg = cfg
        self.store = store
        self.queue: asyncio.Queue[CourseJob] = asyncio.Queue()
        self.by_chat: dict[int, CourseJob] = {}
        self.by_user: dict[int, CourseJob] = {}
        self._worker: Optional[asyncio.Task] = None

    def start(self, bot) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(bot))

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass

    def submit(self, job: CourseJob) -> None:
        self.by_chat[job.chat_id] = job
        self.by_user[job.user_id] = job
        self.queue.put_nowait(job)

    def cancel_chat(self, chat_id: int) -> Optional[CourseJob]:
        job = self.by_chat.get(chat_id)
        if job:
            job.cancel.set()
        return job

    async def _run(self, bot) -> None:
        while True:
            job = await self.queue.get()
            try:
                await self._process(bot, job)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("course job crashed")
                await safe_send(bot, job.chat_id, "❌ Course job crashed — see bot logs.")
            finally:
                self.by_chat.pop(job.chat_id, None)
                if self.by_user.get(job.user_id) is job:
                    self.by_user.pop(job.user_id, None)
                self.queue.task_done()

    async def _process(self, bot, job: CourseJob) -> None:
        label = escape(job.folder_name or job.folder_id)
        width = len(str(job.total))
        preview = "\n".join(
            f"{i:>{width}}. {escape(f.rel_path or f.name)}"
            for i, f in enumerate(job.files[:10], start=1)
        )
        if job.total > 10:
            preview += f"\n… +{job.total - 10} more"
        await safe_send(
            bot,
            job.chat_id,
            f"📚 <b>Sending in sequence</b> — {label}\n"
            f"{job.total} file(s), starting now.\n\n{preview}\n\n"
            "One file at a time, in this order. /cancel to stop, /jobs for progress.",
        )
        for idx, info in enumerate(job.files, start=1):
            if job.cancel.is_set():
                await safe_send(
                    bot,
                    job.chat_id,
                    f"🛑 Stopped at {idx - 1}/{job.total}. "
                    "Send the folder link again to resume from here.",
                )
                return
            job.index = idx - 1
            if self.store.is_done(job.chat_id, info.id):
                job.skipped += 1
                continue
            ok = await file_job(
                bot, job.chat_id, info, cancel=job.cancel, job=job, index=idx
            )
            if ok:
                job.sent += 1
                self.store.mark_done(job.chat_id, info.id, info.name, info.size)
            else:
                job.failed += 1

        await safe_send(
            bot,
            job.chat_id,
            "✅ <b>Course finished</b>\n\n"
            f"Sent: {job.sent}\n"
            f"Already sent earlier (skipped): {job.skipped}\n"
            f"Failed: {job.failed}\n"
            f"Total: {job.total}",
        )


STATE: Optional["BotState"] = None  # set by build_application()


# --------------------------------------------------------------------------- #
# Helpers: progress, uploads
# --------------------------------------------------------------------------- #
class ProgressMessage:
    """Throttled progress reporter: edits a single message in place."""

    def __init__(
        self,
        bot,
        chat_id: int,
        message=None,
        header: str = "",
        interval: float = 3.0,
    ) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.message = message
        self.header = header
        self.interval = interval
        self.started = time.time()
        self.last_edit = 0.0
        self.last_text = ""

    async def __call__(self, done: int, total: Optional[int]) -> None:
        now = time.time()
        if now - self.last_edit < self.interval:
            return
        self.last_edit = now
        text = self.render(done, total)
        if text == self.last_text:
            return
        self.last_text = text
        await self._edit(text)

    async def _edit(self, text: str) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit_text(
                text, parse_mode=ParseMode.HTML,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except RetryAfter:
            pass
        except (BadRequest, TimedOut, NetworkError):
            pass

    def render(self, done: int, total: Optional[int]) -> str:
        elapsed = max(time.time() - self.started, 0.001)
        rate = done / elapsed
        if total:
            frac = min(done / total, 1.0)
            return (
                f"{self.header}\n"
                f"{progress_bar(frac)}  {frac * 100:.1f}%\n"
                f"{human_bytes(done)} / {human_bytes(total)}"
                f"  ·  {human_rate(rate)}  ·  ETA {eta_text(done, total, elapsed)}"
            )
        return (
            f"{self.header}\n"
            f"{progress_bar(0.15)}  {human_bytes(done)}\n{human_rate(rate)}"
        )

    async def finish(self, text: str) -> None:
        await self._edit(text)


class TrackedReader:
    """File wrapper that records how many bytes Telegram has pulled.

    telegram's uploader reads the file object, so this gives real upload
    progress without buffering anything extra.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.size = path.stat().st_size
        self.done = 0
        self._fh = open(path, "rb")

    def read(self, n: int = -1) -> bytes:
        data = self._fh.read(n)
        self.done += len(data)
        return data

    def seek(self, *a, **kw):
        return self._fh.seek(*a, **kw)

    def tell(self):
        return self._fh.tell()

    @property
    def name(self) -> str:
        return self.path.name

    def fileno(self):
        return self._fh.fileno()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class UploadProgressWatcher:
    """Polls a TrackedReader and repaints the Telegram message."""

    def __init__(self, reporter: ProgressMessage, reader: TrackedReader, header: str):
        self.reporter = reporter
        self.reader = reader
        self.header = header
        self._task: Optional[asyncio.Task] = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(3)
            await self.reporter(self.reader.done, self.reader.size)

    async def start(self) -> None:
        self.reporter.header = self.header
        self.reporter.started = time.time()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


VIDEO_EXT = {".mp4", ".m4v", ".mkv", ".mov", ".webm", ".avi"}
AUDIO_EXT = {".mp3", ".m4a", ".ogg", ".opus", ".flac", ".wav", ".aac"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def _local_mode() -> bool:
    return bool(STATE and STATE.cfg.bot_api_base_url)


async def send_file_to_chat(bot, chat_id: int, path: Path, caption: str, reply_to=None):
    """Upload as video/audio/photo where that preserves quality and adds
    streaming, otherwise as a document (Telegram never re-encodes documents).

    With a local Bot API server we pass the filesystem path (no copy, 2 GB cap);
    otherwise we stream the file object.
    """
    ext = path.suffix.lower()
    local = _local_mode()
    reader = None if local else TrackedReader(path)
    base = dict(
        chat_id=chat_id,
        caption=caption,
        parse_mode=ParseMode.HTML,
        read_timeout=1800,
        write_timeout=1800,
        connect_timeout=60,
        pool_timeout=120,
    )
    if reply_to:
        base["reply_to_message_id"] = reply_to
    payload = str(path) if local else reader
    try:
        if ext in VIDEO_EXT:
            await bot.send_video(
                video=payload, supports_streaming=True, filename=path.name, **base
            )
        elif ext in AUDIO_EXT:
            await bot.send_audio(audio=payload, filename=path.name, **base)
        elif ext in IMAGE_EXT and path.stat().st_size < 9 * 1024 * 1024:
            await bot.send_photo(photo=payload, **base)
        else:
            await bot.send_document(document=payload, filename=path.name, **base)
    finally:
        if reader is not None:
            reader.close()


async def _upload_single(
    bot, chat_id: int, reporter: ProgressMessage, dest: Path, caption: str, reply_to=None
) -> bool:
    if _local_mode():
        # Telegram reads the file itself; we cannot see its progress. Show a
        # static "uploading" state instead of a frozen 0%.
        reporter.header = f"⬆️ <b>Uploading {escape(dest.name)}</b>"
        await reporter(0, None)
        try:
            await bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
            await send_file_to_chat(bot, chat_id, dest, caption, reply_to=reply_to)
            await reporter.finish(
                f"✅ <b>{escape(dest.name)}</b> — {human_bytes(dest.stat().st_size)}"
            )
            return True
        except (TimedOut, NetworkError, BadRequest, RetryAfter) as exc:
            await reporter.finish(f"⚠️ Upload failed: {escape(str(exc))}")
            return False

    reader = TrackedReader(dest)
    watcher = UploadProgressWatcher(reporter, reader, "⬆️  Uploading to Telegram…")
    reporter.header = "⬆️  <b>Uploading…</b>"
    reporter.started = time.time()
    await watcher.start()
    try:
        await bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
        await send_file_to_chat(bot, chat_id, dest, caption, reply_to=reply_to)
        final = f"✅ <b>{escape(dest.name)}</b> — {human_bytes(dest.stat().st_size)}"
        ok = True
    except RetryAfter as exc:
        await watcher.stop()
        await reporter.finish(f"⏳ Telegram rate-limited ({exc.retry_after}s). Retry shortly.")
        return False
    except (TimedOut, NetworkError) as exc:
        await watcher.stop()
        await reporter.finish(f"⚠️ Upload interrupted: {escape(str(exc))}")
        return False
    except BadRequest as exc:
        await watcher.stop()
        await reporter.finish(f"❌ Telegram rejected the upload: {escape(str(exc))}")
        return False
    finally:
        reader.close()
    await watcher.stop()
    await reporter.finish(final)
    return ok


async def _upload_split(
    bot, chat_id: int, reporter: ProgressMessage, dest: Path, caption: str,
    limit: int, reply_to=None,
) -> bool:
    total = dest.stat().st_size
    count = max(1, -(-total // limit))
    await reporter.finish(
        f"📦 {human_bytes(total)} is over Telegram's {human_bytes(limit)} limit — "
        f"sending as {count} parts."
    )
    for part, idx, cnt in iter_file_parts(dest, limit):
        reader = TrackedReader(part)
        watcher = UploadProgressWatcher(
            reporter, reader, f"⬆️  Part {idx}/{cnt} — {escape(part.name)}"
        )
        reporter.header = f"⬆️  <b>Part {idx}/{cnt}</b> — {escape(part.name)}"
        reporter.started = time.time()
        await watcher.start()
        try:
            await send_file_to_chat(
                bot,
                chat_id,
                part,
                f"<b>{escape(dest.name)}</b> — part {idx}/{cnt}\n"
                f"{human_bytes(part.stat().st_size)} of {human_bytes(total)}",
                reply_to=reply_to,
            )
        except (TimedOut, NetworkError, BadRequest) as exc:
            await watcher.stop()
            await reporter.finish(f"⚠️ Part {idx} failed: {escape(str(exc))}")
            return False
        finally:
            reader.close()
        await watcher.stop()
    await reporter.finish(
        f"✅ <b>Done</b> — {escape(dest.name)} sent in {count} parts.\n"
        "Reassemble with: <code>cat file.zip.part* > file.zip</code>"
    )
    return True


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _cleanup_partials(dest: Path) -> None:
    for candidate in dest.parent.glob(dest.name + ".part*"):
        _safe_unlink(candidate)


def escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# --------------------------------------------------------------------------- #
# Core flow: one file, end to end
# --------------------------------------------------------------------------- #
async def safe_send(bot, chat_id: int, text: str, *, reply_to=None, markup=None):
    """Send HTML; fall back to plain text if Telegram rejects the markup."""
    kwargs = dict(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    if markup:
        kwargs["reply_markup"] = markup
    if reply_to:
        kwargs["reply_to_message_id"] = reply_to
    for attempt in range(3):
        try:
            return await bot.send_message(**kwargs)
        except RetryAfter as exc:
            await asyncio.sleep(min(exc.retry_after, 30))
        except BadRequest:
            kwargs.pop("parse_mode", None)
            kwargs.pop("link_preview_options", None)
        except (TimedOut, NetworkError):
            await asyncio.sleep(2 + attempt * 3)
    try:
        return await bot.send_message(chat_id=chat_id, text=text[:4000])
    except Exception:  # noqa: BLE001
        log.exception("could not deliver status message")
        return None


async def file_job(
    bot,
    chat_id: int,
    info: DriveFile,
    *,
    cancel: Optional[asyncio.Event] = None,
    job: "Optional[CourseJob]" = None,
    index: int = 0,
    reply_to=None,
) -> bool:
    """Download one Drive file and upload it. Used by single links and courses.

    Returns True when the file reached Telegram; always cleans up temp files.
    """
    cfg = STATE.cfg
    cancel = cancel or asyncio.Event()
    prefix = f"[{index}/{job.total}] " if job and job.total else ""

    async def say(text: str) -> None:
        await safe_send(bot, chat_id, text, reply_to=reply_to)

    max_bytes = int(cfg.max_download_gb * 1024**3) if cfg.max_download_gb else None
    if info.size and max_bytes and info.size > max_bytes:
        await say(
            f"⏭ {prefix}skipped <b>{escape(info.name)}</b> — {human_bytes(info.size)} "
            f"is over this bot's {format_gb(max_bytes)} limit."
        )
        return False

    user_dir = STATE.temp_root / str(chat_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    flat = flatten_rel_path(info.rel_path) or info.download_name
    dest = user_dir / (flat if Path(flat).suffix else info.download_name)

    status = await safe_send(
        bot,
        chat_id,
        f"⬇️ {prefix}<b>{escape(info.name)}</b>\n"
        f"{human_bytes(info.size) if info.size else 'size unknown'}"
        + ("  ·  export" if info.is_google_native else ""),
        reply_to=reply_to,
    )
    reporter = ProgressMessage(
        bot, chat_id, status, header=f"⬇️ {prefix}{escape(info.name)}"
    )

    async with STATE.downloads:
        try:
            await bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
            await STATE.client.download(
                info, dest, on_progress=reporter, cancel=cancel, max_bytes=max_bytes
            )
        except CancelledByUser:
            await reporter.finish("🛑 Cancelled. Partial data kept for a resume.")
            return False
        except FileTooLargeError as exc:
            await reporter.finish(f"❌ {escape(str(exc))}")
            return False
        except QuotaExceededError as exc:
            creds = getattr(STATE.client, "credentials", None)
            can_write = bool(creds and getattr(creds, "can_write", False))
            if cfg.auto_quota_fix and can_write:
                # Automatic /quotafix: copy the file into our own Drive and pull
                # the copy. A brand-new object has its own egress quota, so the
                # download goes through; the copy is deleted right after.
                await reporter.finish(
                    f"⏳ {prefix}<b>Drive quota exceeded</b> — auto-fixing: making a "
                    "temporary copy in your Drive…"
                )
                try:
                    await STATE.client.download_via_copy(
                        info.id, dest, on_progress=reporter, cancel=cancel, max_bytes=max_bytes
                    )
                except QuotaExceededError:
                    await reporter.finish(
                        f"❌ {prefix}Even the copy is throttled. Try again later, "
                        "or ask the owner to transfer ownership to your account."
                    )
                    return False
                except DriveError as copy_exc:
                    await reporter.finish(
                        f"⏳ {prefix}<b>Drive quota exceeded</b>\n\n"
                        f"{escape(str(exc))}\n\nauto-fix failed: {escape(str(copy_exc))}"
                    )
                    return False
            else:
                await reporter.finish(
                    f"⏳ {prefix}<b>Drive quota exceeded</b>\n\n{escape(str(exc))}"
                )
                return False
        except GoogleAccessError as exc:
            await reporter.finish(
                f"🔒 {prefix}<b>Not accessible</b>\n\n{escape(str(exc))}"
            )
            return False
        except DriveError as exc:
            await reporter.finish(f"❌ {prefix}Download failed: {escape(str(exc))}")
            return False
        except Exception as exc:  # noqa: BLE001
            log.exception("download crashed")
            await reporter.finish(f"❌ {prefix}Unexpected error: {escape(repr(exc))}")
            return False

    size = dest.stat().st_size
    caption = (
        f"<b>{escape(info.name)}</b>\n{human_bytes(size)}"
        + (f"  ·  {info.modified[:10]}" if info.modified else "")
        + (f"\n<i>{escape(info.rel_path)}</i>" if info.rel_path else "")
    )
    upload_limit = cfg.max_upload_mb * 1024**2
    if not cfg.bot_api_base_url:
        upload_limit = min(upload_limit, TELEGRAM_HARD_LIMIT)

    try:
        if size <= upload_limit:
            return await _upload_single(
                bot, chat_id, reporter, dest, caption, reply_to=reply_to
            )
        return await _upload_split(
            bot, chat_id, reporter, dest, caption, upload_limit, reply_to=reply_to
        )
    finally:
        if not cfg.keep_files:
            _safe_unlink(dest)
            _cleanup_partials(dest)


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #
async def guard(update: Update) -> bool:
    """Reject strangers when ALLOWED_USER_IDS is set."""
    allowed = STATE.cfg.allowed_users
    if not allowed:
        return True
    uid = update.effective_user.id if update.effective_user else 0
    if uid in allowed:
        return True
    log.warning("blocked user %s", uid)
    if update.effective_message:
        await update.effective_message.reply_text(
            "🔒 This bot is private.\n"
            f"Ask the owner to add your id: <code>{uid}</code>",
            parse_mode=ParseMode.HTML,
        )
    return False


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    text = (
        "👋 <b>Google Drive → Telegram</b>\n\n"
        "Send me a Drive link and I'll fetch it and upload it here, "
        "unchanged (no re-compression).\n\n"
        "<b>Supported</b>\n"
        "• <code>drive.google.com/file/d/…/view</code>\n"
        "• <code>docs.google.com/document|spreadsheets|presentation/d/…</code>\n"
        "  (exported to PDF / XLSX)\n"
        "• <code>drive.google.com/drive/folders/…</code> — course mode:\n"
        "  scans subfolders, queues every video/PDF, resumes if interrupted\n"
        "• <code>drive.google.com/uc?id=…</code>\n\n"
        "<b>Commands</b>\n"
        "/auth — show which Google account the bot uses\n"
        "/streamdl &lt;link&gt; — view-only video: video track + audio track "
        "alag download → lossless join → send\n"
        "/merge — join a video track + audio track you already have in chat\n"
        "/quotafix &lt;link&gt; — download quota exceeded? copy → download → delete\n"
        "/jobs — course progress\n"
        "/cancel — stop whatever is running\n"
        "/clear — forget 'already sent' history\n"
        "/status — queue + disk info\n"
        "/doctor — full self-check (keys, token, Drive, disk, ffmpeg)\n"
        "/help — this message\n\n"
        f"Mode: <b>{'authorized' if STATE.client.authorized else 'public only'}</b>"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    client = STATE.client
    if not client.authorized and not client.api_key:
        await update.effective_message.reply_text(
            "🔓 <b>Public-only mode</b>\n\n"
            "No Google credentials configured, so only files shared with "
            "“anyone with the link” can be downloaded.\n\n"
            "To enable private files you own, read <code>README.md</code> → "
            "“Authorizing the bot”. Never paste your personal cookies into a bot.",
            parse_mode=ParseMode.HTML,
        )
        return
    await update.effective_chat.send_action(ChatAction.TYPING)
    try:
        who = await client.whoami()
    except Exception as exc:  # noqa: BLE001
        await update.effective_message.reply_text(
            f"❌ <b>Credentials are configured but unusable</b>\n\n"
            f"{escape(str(exc))}\n\n"
            "<b>Sabse pehle yeh check karo</b> — in teeno ka <i>ek hi</i> OAuth "
            "client hona chahiye:\n"
            "• <code>GOOGLE_CLIENT_ID</code>\n"
            "• <code>GOOGLE_CLIENT_SECRET</code>\n"
            "• <code>GOOGLE_REFRESH_TOKEN</code> (jis client se token bana)\n\n"
            "Fix: Playground → ⚙ <i>Use your own OAuth credentials</i> → wahi "
            "client ID/secret → naya refresh token → env var update → redeploy.\n\n"
            "Detail: <code>python diagnose_credentials.py</code> host ke shell me, "
            "ya <code>/doctor</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    mode = (
        "service account"
        if client.credentials and client.credentials.is_service_account
        else "OAuth user (refresh token)"
    )
    await update.effective_message.reply_text(
        "✅ <b>Google credentials OK</b>\n\n"
        f"Account: <code>{escape(who)}</code>\n"
        f"Mode: {escape(mode)}\n"
        f"Abuse acknowledgement: {'on' if STATE.cfg.acknowledge_abuse else 'off'}\n\n"
        "Anything this account can see in Drive is downloadable.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    chat_id = update.effective_chat.id
    course = STATE.jobs.cancel_chat(chat_id)
    single = STATE.active.get(update.effective_user.id)
    if single:
        single.cancel.set()
    if not course and not single:
        await update.effective_message.reply_text("Nothing is running.")
        return
    await update.effective_message.reply_text(
        "🛑 Cancelling…" + (" (course will stop after the current file)" if course else "")
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    usage = shutil.disk_usage(STATE.temp_root)
    uptime = int(time.time() - STATE.started_at)
    job = STATE.active.get(update.effective_user.id)
    await update.effective_message.reply_text(
        "📊 <b>Status</b>\n\n"
        f"Uptime: {uptime // 3600}h {(uptime % 3600) // 60}m\n"
        f"Your job: {escape(job.file.name) if job else 'idle'}\n"
        f"Parallel downloads: {STATE.cfg.concurrent_downloads}\n"
        f"Disk free: {human_bytes(usage.free)}\n"
        f"Python: {platform.python_version()}\n"
        f"Temp dir: <code>{escape(str(STATE.temp_root))}</code>",
        parse_mode=ParseMode.HTML,
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the preview keyboard: start / cancel / list-only a folder scan."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    if STATE.cfg.allowed_users and user_id not in STATE.cfg.allowed_users:
        await query.edit_message_text("🔒 Not allowed.")
        return

    if data == "noop":
        return

    if data == "cancel":
        job = STATE.jobs.cancel_chat(chat_id)
        pending = PENDING.pop(chat_id, None)
        if job:
            await query.edit_message_text("🛑 Stopping after the current file…")
        elif pending:
            await query.edit_message_text("🛑 Folder transfer cancelled.")
        else:
            await query.edit_message_text("Nothing to cancel.")
        return

    if data == "reset":
        STATE.store.reset(chat_id)
        job = STATE.jobs.by_chat.get(chat_id)
        if job:
            job.cancel.set()
        PENDING.pop(chat_id, None)
        await query.edit_message_text(
            "♻️ History cleared for this chat. Send the folder link again "
            "to re-send everything from the start."
        )
        return

    if data.startswith("list:"):
        folder_id = data.split(":", 1)[1]
        await query.edit_message_text("⏳ Scanning folder…")
        try:
            files = await STATE.client.list_folder(
                folder_id, max_files=STATE.cfg.max_folder_files
            )
        except DriveError as exc:
            await query.edit_message_text(f"🔒 {exc}")
            return
        lines = [f"📁 <b>{len(files)} file(s)</b> (top level)"]
        for i, f in enumerate(files[:60], 1):
            lines.append(
                f"{i}. {escape(f.name)} — {human_bytes(f.size) if f.size else '?'}"
            )
        if len(files) > 60:
            lines.append(f"… and {len(files) - 60} more")
        await query.edit_message_text(
            "\n".join(lines), parse_mode=ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return

    if data.startswith("start:"):
        folder_id = data.split(":", 1)[1]
        pending = PENDING.pop(chat_id, None)
        await query.edit_message_text("⏳ Preparing the course transfer…")
        await start_folder_job(context.bot, chat_id, user_id, folder_id)
        return


PENDING: dict[int, list[DriveFile]] = {}


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    from gdrive_core import parse_drive_links

    message = update.effective_message
    refs = parse_drive_links(message.text or "", limit=5)
    if not refs:
        await safe_send(
            context.bot,
            update.effective_chat.id,
            "Send a Google Drive link, e.g.\n"
            "<code>https://drive.google.com/file/d/FILE_ID/view</code>\n"
            "or a folder: <code>https://drive.google.com/drive/folders/FOLDER_ID</code>",
            reply_to=message.message_id,
        )
        return

    for ref in refs:
        if ref.kind == "folder":
            await offer_folder(context.bot, update, ref.file_id)
            continue
        info = await _safe_meta(context.bot, update, ref.file_id)
        if info is None:
            continue
        await file_job(
            context.bot,
            update.effective_chat.id,
            info,
            reply_to=message.message_id,
        )


async def _safe_meta(bot, update, file_id: str) -> Optional[DriveFile]:
    """Fetch metadata, reporting failures to the user once."""
    try:
        return await STATE.client.get_file(file_id)
    except DriveError as exc:
        await safe_send(
            bot, update.effective_chat.id,
            f"🔒 <b>Cannot open that file</b>\n\n{escape(str(exc))}",
            reply_to=update.effective_message.message_id,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        log.exception("metadata failed")
        await safe_send(
            bot, update.effective_chat.id,
            f"❌ Metadata lookup failed: {escape(repr(exc))}",
        )
        return None


async def offer_folder(bot, update: Update, folder_id: str) -> None:
    """Scan the folder and ask for confirmation before sending hundreds of files."""
    chat_id = update.effective_chat.id
    await safe_send(
        bot, chat_id, "🔍 Scanning folder (subfolders included)…",
        reply_to=update.effective_message.message_id,
    )
    try:
        files = [
            f
            async for f in STATE.client.iter_folder(
                folder_id,
                recursive=STATE.cfg.folder_recursive,
                max_files=STATE.cfg.max_folder_files,
                order=STATE.cfg.folder_order,
            )
        ]
    except DriveError as exc:
        await safe_send(bot, chat_id, f"🔒 {escape(str(exc))}")
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("folder scan failed")
        await safe_send(bot, chat_id, f"❌ Folder scan failed: {escape(repr(exc))}")
        return

    if not files:
        await safe_send(
            bot, chat_id,
            "That folder has no downloadable files (or only shortcuts I couldn't resolve).",
        )
        return

    total = sum((f.size or 0) for f in files)
    already = sum(1 for f in files if STATE.store.is_done(chat_id, f.id))
    width = len(str(len(files)))
    preview = "\n".join(
        f"{i:>{width}}. {escape(f.rel_path or f.name)}"
        + (f" — {human_bytes(f.size)}" if f.size else "")
        for i, f in enumerate(files[:12], start=1)
    )
    if len(files) > 12:
        preview += f"\n… and {len(files) - 12} more"

    PENDING[chat_id] = files
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("▶️ Send all to Telegram", callback_data=f"start:{folder_id}")],
            [
                InlineKeyboardButton("📋 List only", callback_data=f"list:{folder_id}"),
                InlineKeyboardButton("✖️ Cancel", callback_data="cancel"),
            ],
            [InlineKeyboardButton("♻️ Reset history", callback_data="reset")],
        ]
    )
    await safe_send(
        bot,
        chat_id,
        f"📚 <b>Folder ready</b> — sequence preview\n\n"
        f"Files: <b>{len(files)}</b>\n"
        f"Total size: <b>{human_bytes(total) if total else 'unknown'}</b>\n"
        f"Already sent earlier: {already} (will be skipped)\n\n"
        f"{preview}\n\n"
        "Send in this sequence? You can stop any time with /cancel.",
        markup=markup,
    )


async def start_folder_job(bot, chat_id: int, user_id: int, folder_id: str) -> None:
    files = PENDING.get(chat_id)
    if not files:
        try:
            files = [
                f
                async for f in STATE.client.iter_folder(
                    folder_id,
                    recursive=STATE.cfg.folder_recursive,
                    max_files=STATE.cfg.max_folder_files,
                    order=STATE.cfg.folder_order,
                )
            ]
        except DriveError as exc:
            await safe_send(bot, chat_id, f"🔒 {escape(str(exc))}")
            return
    if not files:
        await safe_send(bot, chat_id, "Nothing to send.")
        return
    job = CourseJob(chat_id, user_id, folder_id)
    job.files = files
    STATE.jobs.submit(job)


async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    chat_id = update.effective_chat.id
    job = STATE.jobs.by_chat.get(chat_id)
    done = STATE.store.count_done(chat_id)
    if not job:
        await safe_send(
            context.bot, chat_id,
            f"No course running right now.\nFiles sent earlier from this chat: {done}.",
        )
        return
    cur = job.current
    await safe_send(
        context.bot, chat_id,
        f"📊 <b>Course in progress</b>\n\n"
        f"File {job.index + 1} of {job.total} in sequence\n"
        f"Current: {escape(cur.name) if cur else '—'}\n"
        f"Sent: {job.sent} · Skipped: {job.skipped} · Failed: {job.failed}\n"
        f"Elapsed: {int((time.time() - job.started) // 60)} min\n\n"
        "Queue: one course at a time. /cancel to stop.",
    )


async def cmd_merge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Join a separate video track + audio track into one MP4 (stream copy).

    This is the step the YouTube tutorials handle by "open it in a video editor" —
    which means a re-encode. FFmpeg with -c copy does the same job losslessly in
    seconds, which is what we do here.
    """
    if not await guard(update):
        return
    chat_id = update.effective_chat.id
    msg = update.effective_message

    if not media_mux.available():
        await safe_send(
            context.bot,
            chat_id,
            "❌ <b>ffmpeg missing</b> on this server, so tracks cannot be joined.\n\n"
            "Docker deploy: rebuild with the current Dockerfile (it installs ffmpeg).\n"
            "VPS: <code>apt-get install -y ffmpeg</code>",
        )
        return

    video = msg.video or (msg.document if (msg.document and "video" in (msg.document.mime_type or "")) else None)
    audio = msg.audio or (msg.document if (msg.document and "audio" in (msg.document.mime_type or "")) else None)
    reply = msg.reply_to_message

    # Two ways to supply the tracks: reply to one + attach the other, or send both
    # in one message (video + audio uploaded together).
    if video and not audio and reply:
        audio = reply.audio or (reply.document if reply.document and "audio" in (reply.document.mime_type or "") else None)
    if audio and not video and reply:
        video = reply.video or (reply.document if reply.document and "video" in (reply.document.mime_type or "") else None)

    if not (video and audio):
        await safe_send(
            context.bot,
            chat_id,
            "<b>Track merger</b>\n\n"
            "Do tracks ko bhejo: <b>video</b> ek message me, <b>audio</b> doosre me, "
            "phir audio wale message ko video wale ka <i>reply</i> bana ke "
            "<code>/merge</code> bhejo.\n\n"
            "Ya dono files ek hi message me bhejo aur usme <code>/merge</code> likho "
            "(caption me).\n\n"
            "Merge ffmpeg se hota hai — <b>stream copy</b>, matlab quality bilkul "
            "waisi hi rehti hai, re-encode nahi.",
        )
        return

    status = await safe_send(context.bot, chat_id, "🎬 Tracks download ho rahe hain…")
    work = STATE.temp_root / str(chat_id) / "merge"
    work.mkdir(parents=True, exist_ok=True)

    try:
        v_file = await video.get_file()
        v_path = work / (video.file_name or "video.mp4")
        await v_file.download_to_drive(str(v_path))
        a_file = await audio.get_file()
        a_path = work / (audio.file_name or "audio.m4a")
        await a_file.download_to_drive(str(a_path))
    except Exception as exc:  # noqa: BLE001
        await safe_send(context.bot, chat_id, f"❌ Track download failed: {escape(str(exc))}")
        return

    await status.edit_text("🔧 Joining (stream copy, no re-encode)…") if status else None
    try:
        result = await media_mux.mux(v_path, a_path, out=work / "merged.mp4")
        ok, detail = await media_mux.verify(result)
    except media_mux.MuxError as exc:
        await safe_send(context.bot, chat_id, f"❌ Merge failed: {escape(str(exc))}")
        return

    size = result.path.stat().st_size
    caption = (
        f"🎬 <b>Merged</b> · {human_bytes(size)}\n"
        f"<code>{escape(detail)}</code>\n"
        f"<i>video copied as-is, audio {'re-encoded to AAC' if result.reencoded_audio else 'copied as-is'}</i>"
        + ("" if ok else "\n⚠️ verify warning: " + escape(detail))
    )
    try:
        await send_file_to_chat(context.bot, chat_id, result.path, caption)
    finally:
        if not STATE.cfg.keep_files:
            for f in work.glob("*"):
                _safe_unlink(f)


async def cmd_streamdl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Full "view-only video" pipeline: two stream tracks -> mux -> Telegram.

    This is the whole tutorial from the video, automated end to end:

      1. open the Drive preview and pull out the video-only + audio-only
         `videoplayback` URLs   (gdrive_core.get_stream_parts)
      2. strip the `&range=` tail so the FULL track downloads  (media_download)
      3. download both tracks with resume + retries
      4. join them with ffmpeg **stream copy** — no re-encode, zero quality loss
         (the video tells you to use a video editor here, which re-encodes)
      5. verify the output really has both tracks, then upload to Telegram

    Works for public / link-shared view-only files. Private files go through the
    normal /auth path instead (they have a proper download endpoint).
    """
    if not await guard(update):
        return
    from gdrive_core import parse_drive_links

    chat_id = update.effective_chat.id
    args = " ".join(context.args or "")
    quoted = getattr(update.effective_message, "reply_to_message", None)
    if not args and quoted is not None:
        args = quoted.text or ""

    refs = parse_drive_links(args)
    if not refs:
        await safe_send(
            context.bot,
            chat_id,
            "<b>View-only video pipeline</b>\n\n"
            "Usage: <code>/streamdl &lt;Drive video link&gt;</code>\n\n"
            "Video ka download button band hai par preview chalta hai? Yeh "
            "command wahi karti hai jo tutorial me manually karte hain — "
            "video track aur audio track alag download karke <b>losslessly</b> "
            "jodti hai, phir Telegram pe bhejti hai.\n\n"
            "<i>Sirf public / link-shared files ke liye. Private course files "
            "ke liye normal link bhejo (OAuth se chalega).</i>",
        )
        return

    if not media_mux.available():
        await safe_send(
            context.bot,
            chat_id,
            "❌ <b>ffmpeg missing</b> on this server, so the tracks cannot be "
            "joined.\n\nDocker deploy: rebuild with the current Dockerfile "
            "(it installs ffmpeg).\nVPS: <code>apt-get install -y ffmpeg</code>",
        )
        return

    file_id = refs[0].file_id
    work = STATE.temp_root / str(chat_id) / f"stream-{file_id[:10]}"
    work.mkdir(parents=True, exist_ok=True)
    status = await safe_send(context.bot, chat_id, "🔎 Reading the stream manifest…")

    # Best-effort name for the caption: the stream page itself has none.
    title = file_id
    try:
        meta = await STATE.client.get_file(file_id)
        if meta and meta.name:
            title = meta.name
    except Exception:  # noqa: BLE001 - anonymous metadata can be refused
        pass

    async def log_line(text: str) -> None:
        await safe_send(context.bot, chat_id, text)

    # ---- step 1+2: find the two track URLs --------------------------------- #
    try:
        video_url, audio_url = await STATE.client.get_stream_parts(file_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("stream parts failed")
        await safe_send(context.bot, chat_id, f"❌ Could not read the stream links: {escape(repr(exc))}")
        return

    if not video_url:
        await safe_send(
            context.bot,
            chat_id,
            "❌ <b>No stream found</b>\n\n"
            "Matlab yeh file ya (a) public nahi hai — sirf aapke account ke "
            "through khulti hai, ya (b) video nahi hai, ya (c) DRM-protected "
            "hai.\n\n"
            "<b>Private file ke liye:</b> link seedha bhejo, bot OAuth se "
            "download karega (agar /auth me account dikh raha hai).",
        )
        return

    await safe_send(
        context.bot,
        chat_id,
        "🎞 <b>Tracks mile</b>\n"
        f"video: {'✅' if video_url else '❌'}  ·  audio: {'✅' if audio_url else '❌'}\n"
        "<i>Google DASH me video aur audio alag stream karta hai — isi liye "
        "normal download me audio gayab lagta hai.</i>",
    )

    # ---- step 3: download both tracks ------------------------------------- #
    # ProgressMessage throttles edits to once every 3 s — editing on every 1 MiB
    # chunk would get the bot rate-limited by Telegram within a minute.
    cancel = asyncio.Event()
    sess = await STATE.client.session()
    video_track = None
    audio_track = None

    async def download_one(url: str, dest: Path, label: str, kind: str):
        head = await safe_send(
            context.bot, chat_id, f"⬇️ Downloading the <b>{label}</b> track…"
        )
        reporter = ProgressMessage(
            context.bot, chat_id, head, header=f"⬇️ {label} track"
        )
        try:
            track = await media_download.fetch_track(
                sess, url, dest, kind=kind, on_progress=reporter, cancel=cancel
            )
        except media_download.StreamFetchError as exc:
            await reporter.finish(f"❌ {label} track failed: {escape(str(exc))}")
            raise
        await reporter.finish(
            f"✅ {label} track — {human_bytes(track.bytes_written)}"
            + ("  <i>(resumed)</i>" if track.resumed else "")
        )
        return track

    try:
        video_track = await download_one(
            video_url, work / ("video.webm" if "webm" in video_url else "video.mp4"),
            "video", "video",
        )
        if audio_url:
            audio_track = await download_one(
                audio_url, work / "audio.m4a", "audio", "audio"
            )
    except media_download.StreamFetchError:
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("stream download crashed")
        await safe_send(
            context.bot, chat_id, f"❌ Track download crashed: {escape(repr(exc))}"
        )
        return

    if audio_track is None:
        await safe_send(
            context.bot,
            chat_id,
            "ℹ️ No separate audio track found — this stream already carries its "
            "audio, so nothing needs joining.",
        )

    # ---- step 4: mux (stream copy) ---------------------------------------- #
    # WebM renditions (VP9/Opus) cannot live in an .mp4 without re-encoding, so
    # pick the container from the codecs rather than assuming mp4.
    out = work / ("final.mkv" if "webm" in video_url else "final.mp4")
    try:
        if status:
            await status.edit_text("🔧 Joining tracks — stream copy, no re-encode…")
        result = await media_mux.mux(
            video_track.path,
            audio_track.path if audio_track else None,
            out=out,
        )
        ok, detail = await media_mux.verify(result) if audio_track else (True, result.summary)
    except media_mux.MuxError as exc:
        await safe_send(context.bot, chat_id, f"❌ Merge failed: {escape(str(exc))}")
        return

    if audio_track and not ok:
        await safe_send(
            context.bot,
            chat_id,
            f"⚠️ Merge verification warning: {escape(detail)}\n"
            "File bhej raha hoon, par audio check kar lo.",
        )

    # ---- step 5: upload ---------------------------------------------------- #
    size = result.path.stat().st_size
    saved = ""
    if audio_track:
        raw = video_track.bytes_written + audio_track.bytes_written
        if size and raw:
            saved = f" · mux ne {human_bytes(raw)} tracks ko {human_bytes(size)} me join kiya"
    caption = (
        f"🎬 <b>{escape(title)}</b>\n"
        f"{human_bytes(size)}{saved}\n"
        f"<code>{escape(detail)}</code>\n"
        "<i>stream copy — quality exactly as Google served it</i>"
    )
    # Respect the same upload ceiling as normal files: Telegram's public API
    # caps bots at 50 MB (documented), so anything bigger is split into parts
    # instead of failing at 60%.
    cfg = STATE.cfg
    upload_limit = cfg.max_upload_mb * 1024**2
    if not cfg.bot_api_base_url:
        upload_limit = min(upload_limit, TELEGRAM_HARD_LIMIT)
    try:
        if size > upload_limit:
            reporter = ProgressMessage(
                context.bot, chat_id, status, header="⬆️ Uploading joined video"
            )
            await _upload_split(
                context.bot, chat_id, reporter, result.path, caption, upload_limit
            )
        else:
            await send_file_to_chat(context.bot, chat_id, result.path, caption)
            if status:
                await status.edit_text(f"✅ Done — {detail}")
    except Exception as exc:  # noqa: BLE001
        await safe_send(context.bot, chat_id, f"⚠️ Upload failed: {escape(str(exc))}")
    finally:
        if not STATE.cfg.keep_files:
            for f in work.glob("*"):
                _safe_unlink(f)
            try:
                work.rmdir()
            except OSError:
                pass


async def cmd_quotafix(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quota-safe download: copy the file into our own Drive, send the copy.

    Google throttles egress per file object. A fresh copy is a new object, so it
    downloads immediately. The copy is deleted right after upload.
    """
    if not await guard(update):
        return
    from gdrive_core import parse_drive_links

    chat_id = update.effective_chat.id
    refs = parse_drive_links(" ".join(context.args or []))
    if not refs:
        await safe_send(
            context.bot,
            chat_id,
            "Usage: <code>/quotafix &lt;Drive file link&gt;</code>\n"
            "Use this when a file fails with \"download quota exceeded\".",
        )
        return

    creds = STATE.client.credentials
    if not creds or not getattr(creds, "can_write", False):
        await safe_send(
            context.bot,
            chat_id,
            "🔒 <b>Full Drive access needed</b>\n\n"
            "The quota fix works by creating a temporary copy in your Drive, which "
            "needs write scope. Right now the bot has <b>read-only</b> access.\n\n"
            "Enable it:\n"
            "1. Set <code>DRIVE_SCOPE=full</code> in your host's env vars\n"
            "2. Re-authorize with the full scope (setup_wizard / oauth_setup)\n"
            "3. Redeploy, then /quotafix again\n\n"
            "<i>Only do this if you're comfortable with the bot being able to "
            "create and delete files it made itself. It never touches your other "
            "files.</i>",
        )
        return

    file_id = refs[0].file_id
    user_dir = STATE.temp_root / str(chat_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    status = await safe_send(
        context.bot, chat_id, "🩹 Making a temporary copy in your Drive…"
    )
    reporter = ProgressMessage(context.bot, chat_id, status, header="⬇️ Quota-safe download")

    try:
        info = await STATE.client.get_file(file_id)
    except DriveError as exc:
        await reporter.finish(f"❌ {escape(str(exc))}")
        return

    dest = user_dir / info.download_name
    try:
        await STATE.client.download_via_copy(
            file_id, dest, on_progress=reporter, max_bytes=int(STATE.cfg.max_download_gb * 1024**3)
        )
    except QuotaExceededError as exc:
        await reporter.finish(f"❌ Copy also throttled: {escape(str(exc))}")
        return
    except DriveError as exc:
        await reporter.finish(f"❌ {escape(str(exc))}")
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("quotafix failed")
        await reporter.finish(f"❌ Copy download failed: {escape(repr(exc))}")
        return

    caption = f"<b>{escape(info.name)}</b>\n{human_bytes(dest.stat().st_size)} · quota-safe copy"
    try:
        await send_file_to_chat(context.bot, chat_id, dest, caption)
        await reporter.finish("✅ Sent. Temporary copy deleted from your Drive.")
    except Exception as exc:  # noqa: BLE001
        await reporter.finish(f"⚠️ Download ok, upload failed: {escape(str(exc))}")
    finally:
        if not STATE.cfg.keep_files:
            _safe_unlink(dest)


async def cmd_doctor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Self-diagnosis straight in the chat — no shell needed."""
    if not await guard(update):
        return
    chat_id = update.effective_chat.id
    msg = await safe_send(context.bot, chat_id, "🩺 Running checks…")
    folder_id = ""
    if context.args:
        from gdrive_core import parse_drive_links

        refs = parse_drive_links(" ".join(context.args))
        if refs:
            folder_id = refs[0].file_id
    try:
        checks = await health_mod.run_all(context.bot, STATE.cfg, STATE.client, folder_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("health check failed")
        await safe_send(context.bot, chat_id, f"❌ Health check crashed: {escape(repr(exc))}")
        return
    text = health_mod.render(checks)
    if msg is not None:
        try:
            await msg.edit_text(text, parse_mode=ParseMode.HTML)
            return
        except BadRequest:
            pass
    await safe_send(context.bot, chat_id, text)


async def cmd_fixwebhook(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete a stale webhook — the classic reason a polling bot looks dead."""
    if not await guard(update):
        return
    chat_id = update.effective_chat.id
    try:
        await context.bot.delete_webhook(drop_pending_updates=True)
        await safe_send(
            context.bot,
            chat_id,
            "🧹 Webhook delete ho gaya. Ab polling kaam karegi — 5 second ruko, "
            "phir /doctor bhejo. (Restart ke baad bhi ye setting bani rahegi.)",
        )
    except Exception as exc:  # noqa: BLE001
        await safe_send(context.bot, chat_id, f"❌ Webhook delete fail: {escape(repr(exc))}")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wipe the 'already sent' ledger for this chat."""
    if not await guard(update):
        return
    chat_id = update.effective_chat.id
    job = STATE.jobs.cancel_chat(chat_id)
    STATE.store.reset(chat_id)
    PENDING.pop(chat_id, None)
    await safe_send(
        context.bot, chat_id,
        ("🛑 Stopped the running job. " if job else "")
        + "♻️ History cleared — the next folder link will upload everything again.",
    )


# --------------------------------------------------------------------------- #
# Bootstrapping
# --------------------------------------------------------------------------- #
def build_application(cfg: BotConfig) -> Application:
    global STATE
    STATE = BotState(cfg)
    builder = (
        ApplicationBuilder()
        .token(cfg.bot_token)
        .concurrent_updates(True)
        .rate_limiter(AIORateLimiter(max_retries=3))
        .read_timeout(1800)
        .write_timeout(1800)
        .connect_timeout(60)
        .pool_timeout(120)
    )
    if cfg.bot_api_base_url:
        builder = builder.base_url(cfg.bot_api_base_url)
        # local_mode lets PTB hand Telegram a *path* instead of streaming
        # multipart: uploads up to 2000 MB, no local download of our own file,
        # far less RAM. Without it a 2 GB course video cannot be sent at all.
        builder = builder.local_mode(True)

    # Uploads of multi-GB video need a long socket write timeout. PTB 21 keeps
    # a separate `media_write_timeout` for file uploads whose default is 20 s --
    # the classic "large upload dies at 20 seconds" bug. Raise it for media.
    global MEDIA_WRITE_TIMEOUT_CONFIGURED
    MEDIA_WRITE_TIMEOUT_CONFIGURED = float(os.getenv("MEDIA_WRITE_TIMEOUT", "1800") or 1800)
    builder = builder.media_write_timeout(MEDIA_WRITE_TIMEOUT_CONFIGURED)

    app = builder.build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("auth", cmd_auth))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("jobs", cmd_jobs))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("doctor", cmd_doctor))
    app.add_handler(CommandHandler("streamdl", cmd_streamdl))
    app.add_handler(CommandHandler("quotafix", cmd_quotafix))
    app.add_handler(CommandHandler("merge", cmd_merge))
    app.add_handler(CommandHandler("health", cmd_doctor))
    app.add_handler(CommandHandler("fixwebhook", cmd_fixwebhook))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    async def _post_init(application: Application) -> None:
        me = await application.bot.get_me()
        log.info("Bot up as @%s (id %s)", me.username, me.id)
        try:
            info = await application.bot.get_webhook_info()
            if info.url:
                log.warning(
                    "A webhook is registered (%s). Polling will hit 409 Conflict and "
                    "the bot will look silent. Send /fixwebhook or call deleteWebhook.",
                    info.url,
                )
        except Exception:  # noqa: BLE001
            pass
        STATE.jobs.start(application.bot)
        await _startup_report(application)
        if _env_bool("HEALTHCHECK_ENABLED"):
            # Cloud Run / Render / Fly expect something listening on $PORT.
            from healthcheck import serve_in_background

            serve_in_background()
        await application.bot.set_my_commands(
            [
                ("start", "How to use"),
                ("auth", "Which Google account am I using"),
                ("cancel", "Cancel the running transfer"),
                ("jobs", "Course progress"),
                ("clear", "Forget already-sent history"),
                ("status", "Queue and disk info"),
                ("streamdl", "View-only video: 2 tracks -> mux -> send"),
            ]
        )

    async def _startup_report(application: Application) -> None:
        """Ping the admin with a one-line health summary on every boot.

        Without this, a bot that starts but cannot reach Drive silently looks
        'online'. Set STARTUP_REPORT=false to disable.
        """
        if not _env_bool("STARTUP_REPORT", True):
            return
        targets = sorted(STATE.cfg.allowed_users)
        if not targets:
            return  # no admin configured; don't spam a random chat
        try:
            checks = await health_mod.run_all(application.bot, STATE.cfg, STATE.client)
        except Exception:  # noqa: BLE001
            log.exception("startup health check failed")
            return
        text = health_mod.compact(checks)
        bad = [c for c in checks if c.ok is False]
        if bad:
            text += "\n\n/doctor se poora detail, /fixwebhook agar webhook issue ho."
        for uid in targets[:3]:
            try:
                await application.bot.send_message(
                    uid, text, parse_mode=ParseMode.HTML,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            except Exception:  # noqa: BLE001
                log.info("could not send startup report to %s", uid)

    async def _post_shutdown(application: Application) -> None:
        await STATE.jobs.stop()
        await STATE.client.close()
        if not cfg.keep_files:
            shutil.rmtree(STATE.temp_root, ignore_errors=True)

    app.post_init = _post_init
    app.post_shutdown = _post_shutdown
    return app


def check_config() -> int:
    """--check: validate import graph and env without touching Telegram."""
    cfg = BotConfig.from_env()
    problems = 0
    if not cfg.bot_token or ":" not in cfg.bot_token:
        print("✗ BOT_TOKEN missing or malformed (expect 123456:ABC-DEF…)")
        problems += 1
    else:
        print("✓ BOT_TOKEN looks well-formed")
    if cfg.service_account_json:
        import json as _json

        raw = cfg.service_account_json
        if raw.lstrip().startswith("{"):
            try:
                info = _json.loads(raw)
                print(f"✓ GOOGLE_SERVICE_ACCOUNT_JSON parsed (client_email={info.get('client_email', '?')})")
            except ValueError as exc:
                print(f"✗ GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON: {exc}")
                problems += 1
        else:
            ok = Path(raw).is_file()
            print(f"{'✓' if ok else '✗'} service account path from env: {raw}")
            problems += 0 if ok else 1
    if cfg.service_account_file:
        ok = Path(cfg.service_account_file).is_file()
        print(f"{'✓' if ok else '✗'} service account file: {cfg.service_account_file}")
        problems += 0 if ok else 1
    if cfg.refresh_token:
        has_pair = bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))
        print(f"{'✓' if has_pair else '✗'} refresh token present "
              f"({'client id/secret found' if has_pair else 'GOOGLE_CLIENT_ID/SECRET missing'})")
        problems += 0 if has_pair else 1
    if not (cfg.service_account_file or cfg.refresh_token or cfg.service_account_json):
        print("! No credentials: public links only (fine for public files)")
    elif cfg.refresh_token:
        print("→ verifying Google token plumbing: python diagnose_credentials.py")
    print(f"✓ temp dir: {cfg.temp_dir or tempfile.gettempdir()}")
    print(f"✓ parallel downloads: {cfg.concurrent_downloads}")
    print(f"✓ max download: {cfg.max_download_gb} GB, max upload: {cfg.max_upload_mb} MB")
    print(
        f"✓ folder mode: {'recursive' if cfg.folder_recursive else 'top level only'}, "
        f"{cfg.folder_order}-first order, max {cfg.max_folder_files} files, "
        f"ledger={cfg.jobs_db or 'in-memory'}"
    )
    print(f"✓ allowed users: {sorted(cfg.allowed_users) or 'everyone (set ALLOWED_USER_IDS!)'}")
    try:
        from telegram import __version__ as ptb_version

        print(f"✓ python-telegram-bot {ptb_version}")
    except Exception as exc:  # noqa: BLE001
        print(f"✗ python-telegram-bot import failed: {exc}")
        problems += 1
    return 1 if problems else 0


def main() -> None:
    if "--check" in sys.argv:
        sys.exit(check_config())
    cfg = BotConfig.from_env()
    if not cfg.bot_token:
        sys.exit("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    app = build_application(cfg)
    log.info("Starting polling…")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        close_loop=False,
    )


if __name__ == "__main__":
    main()
