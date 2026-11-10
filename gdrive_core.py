"""
gdrive_core.py — Google Drive link parsing + memory-safe, resumable downloader.

Deliberately has NO Telegram dependency so it can be unit-tested on its own.

Access model (the only two modes that exist on purpose):
  * AUTHORIZED  -> you supply OAuth user credentials or a service account.
                   Anything that account can see can be downloaded: your own
                   Drive, files/folders shared with you or with the service
                   account, plus anything public.
  * PUBLIC      -> no credentials; only files the owner shared as
                   "anyone with the link". Works for files >100MB too, using
                   Google's standard virus-scan 'confirm' handshake, which is
                   the normal multi-GB download flow -- not an auth bypass.

Anything else (somebody else's private/locked file) raises GoogleAccessError
with a plain-English explanation instead of trying to defeat the ACL.
"""

from __future__ import annotations

import asyncio
import gzip
import html
import json
import os
import re
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Iterable, Optional
from urllib.parse import parse_qs, urlparse

import aiohttp

# --------------------------------------------------------------------------- #
# Optional Google credential support (only needed for AUTHORIZED mode)
# --------------------------------------------------------------------------- #
try:  # pragma: no cover - import guard
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials as UserCredentials
    from google.auth.transport.requests import Request as GoogleAuthRequest

    GOOGLE_AUTH_AVAILABLE = True
except Exception:  # pragma: no cover
    GOOGLE_AUTH_AVAILABLE = False

# Google serves file bytes from this host; drive.google.com/uc redirects here and
# drops the `confirm` parameter, which breaks large downloads. Keep it overridable
# so tests can point it at a local fake server.
USERCONTENT_DOWNLOAD = "https://drive.usercontent.google.com/download"

DRIVE_SCOPE_READONLY = "https://www.googleapis.com/auth/drive.readonly"
# Only needed for the quota workaround (copy-to-my-Drive + delete the copy).
# Read-only is the default because it is the least privileged thing that works.
DRIVE_SCOPE_FULL = "https://www.googleapis.com/auth/drive"
DRIVE_SCOPE = DRIVE_SCOPE_READONLY
DRIVE_API = "https://www.googleapis.com/drive/v3"
USER_AGENT = "gdrive-telegram-bot/1.0"

# Google-native docs must be *exported*, they have no binary body.
GOOGLE_APPS_PREFIX = "application/vnd.google-apps."
EXPORT_MAP = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.drawing": ("image/png", ".png"),
    "application/vnd.google-apps.script": ("application/vnd.google-apps.script+json", ".json"),
}

CHUNK_SIZE = 1024 * 1024  # 1 MiB per read: constant RAM regardless of file size

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class DriveError(Exception):
    """Base error carrying a message safe to show a Telegram user."""


class GoogleAccessError(DriveError):
    """File exists but this bot may not read it (private / restricted / 404)."""


class CancelledByUser(DriveError):
    pass


class FileTooLargeError(DriveError):
    pass


QUOTA_HELP = (
    "Google Drive's per-file download quota is exhausted (\"too many users have "
    "viewed or downloaded this file\"). This is throttling, not a permission "
    "problem — the file itself is fine.\n\n"
    "What actually fixes it:\n"
    "• /quotafix — makes your own copy in your Drive (new file id = fresh quota), "
    "sends the copy, then deletes it. Needs \"full Drive access\" scope.\n"
    "• Wait 1–24 h — the window is rolling.\n"
    "• Ask the owner to re-upload, or move the file into a Shared Drive.\n"
    "VPN / cookie tricks do NOT work: the limit is on the file object, server-side."
)


class QuotaExceededError(DriveError):
    """Drive's per-file download quota wall (not an ACL problem).

    Google throttles a *file object*, not an account: once a shared file has been
    hammered by many downloaders it returns 403 downloadQuotaExceeded for a
    rolling window. Access is fine; egress is throttled.
    """


# --------------------------------------------------------------------------- #
# Link parsing
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DriveRef:
    kind: str  # "file" | "folder"
    file_id: str


_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")

# /file/d/<id>/, /document/d/<id>/, /spreadsheets/d/<id>/, /presentation/d/<id>/,
# /drive/folders/<id>, /drive/u/0/folders/<id>
_PATH_RE = re.compile(
    r"/(?:file|document|spreadsheets|presentation|drawings|folders)/d/([A-Za-z0-9_-]{10,})"
)
# Drive folder links have no /d/ segment: /drive/folders/<id>, /drive/u/2/folders/<id>
_FOLDER_PATH_RE = re.compile(r"/folders/([A-Za-z0-9_-]{10,})")
_QUERY_KEYS = ("id", "fileid", "file_id", "docid", "folderid")
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def _ref_from_url(url: str) -> Optional[DriveRef]:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if "drive.google.com" not in host and "docs.google.com" not in host:
        return None

    path = parsed.path or ""
    folder = _FOLDER_PATH_RE.search(path)
    if folder and "/folder" in path.lower():
        return DriveRef("folder", folder.group(1))

    m = _PATH_RE.search(path)
    if m:
        kind = "folder" if "/folders/" in path else "file"
        return DriveRef(kind, m.group(1))

    qs = parse_qs(parsed.query)
    for key in _QUERY_KEYS:
        for value in qs.get(key, []):
            if _FILE_ID_RE.match(value):
                kind = "folder" if "folder" in path.lower() else "file"
                return DriveRef(kind, value)

    # Bare "drive.google.com/open?id=..." style is covered above; anything else
    # with a plausible /d/<id> fragment is caught here.
    m = re.search(r"/d/([A-Za-z0-9_-]{10,})", path)
    if m:
        return DriveRef("file", m.group(1))
    return None


def parse_drive_links(text: str, limit: int = 5) -> list[DriveRef]:
    """Extract Drive file/folder references from free text, de-duplicated."""
    found: list[DriveRef] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(text or ""):
        ref = _ref_from_url(match.group(0).rstrip(").,"))
        if ref and ref.file_id not in seen:
            seen.add(ref.file_id)
            found.append(ref)
            if len(found) >= limit:
                break
    return found


def looks_like_drive_link(text: str) -> bool:
    return bool(parse_drive_links(text))


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #
class DriveCredentials:
    """Wraps a refresh-token user credential or a service-account credential.

    Tokens are minted synchronously by google-auth, so refreshing happens in a
    worker thread; the cached token is reused until ~60s before expiry.
    """

    def __init__(
        self,
        *,
        service_account_file: Optional[str] = None,
        service_account_info: Optional[dict] = None,
        client_secrets_file: Optional[str] = None,
        refresh_token: Optional[str] = None,
        subject: Optional[str] = None,
        scope: str = DRIVE_SCOPE_READONLY,
    ) -> None:
        if not GOOGLE_AUTH_AVAILABLE:
            raise DriveError(
                "Google auth libraries are missing. "
                "pip install google-auth google-api-python-client"
            )
        self._creds = None
        self._scope = scope
        self._expiry: float = 0.0
        self._lock = asyncio.Lock()

        if service_account_info:
            # Cloud hosts (Railway/Fly/Render) have no persistent file to mount
            # before first boot, so the key can come from an env var instead.
            self._creds = service_account.Credentials.from_service_account_info(
                service_account_info, scopes=[self._scope]
            )
            if subject:
                self._creds = self._creds.with_subject(subject)
        elif service_account_file:
            if not os.path.isfile(service_account_file):
                raise DriveError(f"Service account file not found: {service_account_file}")
            self._creds = service_account.Credentials.from_service_account_file(
                service_account_file, scopes=[self._scope]
            )
            if subject:  # domain-wide delegation
                self._creds = self._creds.with_subject(subject)
        elif refresh_token:
            if not (
                os.environ.get("GOOGLE_CLIENT_ID")
                and os.environ.get("GOOGLE_CLIENT_SECRET")
            ):
                raise DriveError(
                    "GOOGLE_REFRESH_TOKEN is set but GOOGLE_CLIENT_ID / "
                    "GOOGLE_CLIENT_SECRET are missing. A refresh token only works "
                    "with the exact OAuth client that issued it — add both values "
                    "(see PHONE_CHECKLIST.md Step B)."
                )
            self._creds = UserCredentials(
                token=None,
                refresh_token=refresh_token,
                client_id=os.environ.get("GOOGLE_CLIENT_ID"),
                client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
                token_uri="https://oauth2.googleapis.com/token",
                scopes=[self._scope],
            )
        else:
            raise DriveError(
                "Authorized mode needs GOOGLE_SERVICE_ACCOUNT_FILE or "
                "GOOGLE_REFRESH_TOKEN + GOOGLE_CLIENT_ID/SECRET."
            )

    @property
    def email(self) -> str:
        return getattr(self._creds, "service_account_email", "") or "user-account"

    @property
    def can_write(self) -> bool:
        """True only when the granted scope permits copy/delete (quota fix)."""
        return self._scope == DRIVE_SCOPE_FULL

    @property
    def is_service_account(self) -> bool:
        return bool(getattr(self._creds, "service_account_email", ""))

    async def token(self) -> str:
        async with self._lock:
            now = time.time()
            if self._creds.token and now < self._expiry - 60:
                return self._creds.token
            try:
                await asyncio.to_thread(self._creds.refresh, GoogleAuthRequest())
            except Exception as exc:  # noqa: BLE001
                raise self._explain_refresh_failure(exc) from exc
            self._expiry = (
                self._creds.expiry.timestamp()
                if getattr(self._creds, "expiry", None)
                else time.time() + 3000
            )
            return self._creds.token

    @staticmethod
    def _explain_refresh_failure(exc: Exception) -> DriveError:
        """Turn google-auth's opaque RefreshError into something actionable."""
        raw = str(exc)
        low = raw.lower()
        if "invalid_client" in low or "client was not found" in low:
            return DriveError(
                "Google rejected the credentials: 'invalid_client — OAuth client "
                "not found'.\n\nThe refresh token was issued by a different OAuth "
                "client than GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET, or the client "
                "was deleted.\n\nFix:\n"
                "1. APIs & Services → Credentials → OAuth 2.0 Client IDs: does a "
                "'Desktop app' client exist? If not, create it.\n"
                "2. Copy that client's ID and secret (no quotes, no spaces).\n"
                "3. OAuth Playground → ⚙ gear → tick 'Use your own OAuth "
                "credentials' → paste the same ID + secret.\n"
                "4. Authorize APIs → exchange for tokens → copy the NEW "
                "refresh_token (starts with 1//).\n"
                "5. Update all three env vars together, redeploy, run /auth.\n\n"
                "Diagnose locally: python diagnose_credentials.py"
            )
        if "invalid_grant" in low:
            return DriveError(
                "Google rejected the credentials: 'invalid_grant'. The client is "
                "valid but the refresh token is dead, revoked, expired (Testing "
                "mode tokens die after 7 days), or belongs to another account.\n\n"
                "Fix: Publish the OAuth consent screen, then mint a new refresh "
                "token with the SAME client and update GOOGLE_REFRESH_TOKEN."
            )
        if "unauthorized_client" in low or "deleted_client" in low:
            return DriveError(
                "Google rejected the client (unauthorized_client / deleted_client). "
                "Recreate the OAuth client as type 'Desktop app' and mint a new "
                "refresh token with it."
            )
        if "invalid_scope" in low:
            return DriveError(
                "Google rejected the scope. Re-authorize with "
                "https://www.googleapis.com/auth/drive.readonly"
            )
        return DriveError(f"Google token refresh failed: {raw}")


# --------------------------------------------------------------------------- #
# File metadata
# --------------------------------------------------------------------------- #
@dataclass
class DriveFile:
    id: str
    name: str
    size: Optional[int]
    mime_type: str
    modified: Optional[str] = None
    rel_path: Optional[str] = None  # path inside the scanned folder, e.g. "Module 1/a.mp4"
    # Google's internal streaming endpoint hands out the video track and the audio
    # track as two separate byte-range URLs (Google serves DASH-style, so audio is
    # muxed separately). When we went through that path we keep both parts here so
    # they get muxed back into one MP4 before upload.
    stream_video: Optional[str] = None
    stream_audio: Optional[str] = None

    @property
    def is_google_native(self) -> bool:
        return self.mime_type.startswith(GOOGLE_APPS_PREFIX)

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME

    def export_target(self) -> tuple[str, str]:
        return EXPORT_MAP.get(self.mime_type, ("application/pdf", ".pdf"))

    @property
    def download_name(self) -> str:
        """Filename to write/send, with a sensible extension."""
        name = sanitize_filename(self.name) or self.id
        if self.is_google_native:
            _, ext = self.export_target()
            return name if name.lower().endswith(ext) else name + ext
        if "." not in name:
            ext = guess_extension(self.mime_type)
            if ext:
                return name + ext
        return name


def sanitize_filename(name: str, max_len: int = 120) -> str:
    name = html.unescape(name or "")
    # Characters that break Windows/macOS filesystems or shell usage are
    # neutralised; everything else is preserved.
    name = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]', "_", name).strip().strip(".")
    if len(name) > max_len:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 8:
            name = stem[: max_len - len(ext) - 1] + "." + ext
        else:
            name = name[:max_len]
    return name


def natural_key(text: Optional[str]) -> tuple:
    """"Lesson 2" sorts before "Lesson 10" (human/sequence order, not ASCII)."""
    parts: list[tuple[int, object]] = []
    for chunk in re.split(r"(\d+)", text or ""):
        if not chunk:
            continue
        parts.append((0, int(chunk)) if chunk.isdigit() else (1, chunk.casefold()))
    return tuple(parts)


def flatten_rel_path(rel_path: Optional[str], max_len: int = 110) -> str:
    """"Module 1/Lesson 2/intro.mp4" -> "Module 1 - Lesson 2 - intro.mp4"."""
    if not rel_path:
        return ""
    parts = [p for p in rel_path.split("/") if p]
    if not parts:
        return ""
    joined = " - ".join(parts)
    return sanitize_filename(joined, max_len=max_len)


def guess_extension(mime: str) -> str:
    return {
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "application/zip": ".zip",
        "application/x-zip-compressed": ".zip",
        "application/x-rar-compressed": ".rar",
        "text/plain": ".txt",
        "application/json": ".json",
        "application/vnd.android.package-archive": ".apk",
    }.get((mime or "").lower(), "")


# --------------------------------------------------------------------------- #
# The client
# --------------------------------------------------------------------------- #
ProgressCb = Callable[[int, Optional[int]], Awaitable[None]]


def _body_to_text(raw: bytes) -> str:
    """Decode a response body, inflating gzip first. Safe on HTML/JSON/errors."""
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception:  # noqa: BLE001
            try:
                raw = zlib.decompress(raw, 16 + zlib.MAX_WBITS)
            except zlib.error:
                return ""
    return raw.decode("utf-8", "replace")


async def read_text(response) -> str:
    """Like `response.text()` but tolerant of the gzip bodies this session keeps.

    Diagnostic paths (403 bodies, HTML error pages, the scan-confirmation page)
    must never blow up with UnicodeDecodeError — that masks the real message.
    """
    return _body_to_text(await response.read())


async def read_json(response) -> dict:
    """Parse a Google JSON response even when it arrives gzip-compressed.

    Why this exists: the Drive client's session is created with
    `auto_decompress=False` — that is deliberate, because the *byte pump* must
    stream raw file bytes (Google serves large downloads gzip/chunked and a
    transparently decompressing session corrupts resumable Range requests and
    breaks progress accounting).

    The side effect: every *metadata* response also stays compressed, so
    `response.text()` / `response.json()` handed us gzip bytes and raised
    `UnicodeDecodeError: 'utf-8' codec can't decode byte 0x8b` — the "Metadata
    lookup failed" / "Folder scan failed" bug.

    This helper sniffs the gzip magic bytes and inflates when needed, so the same
    session can serve both purposes.
    """
    raw = await response.read()
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception:  # noqa: BLE001 - truncated/streamed gzip, or corrupt
            try:
                raw = zlib.decompress(raw, 16 + zlib.MAX_WBITS)
            except zlib.error:
                raw = b""
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        text = raw.decode("utf-8", "replace").strip()
        raise DriveError(f"Google returned a non-JSON response: {text[:200]}")


class DriveClient:
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        credentials: Optional[DriveCredentials] = None,
        acknowledge_abuse: bool = False,
        max_retries: int = 5,
        timeout_total: Optional[float] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.api_key = api_key
        self.credentials = credentials
        self.acknowledge_abuse = acknowledge_abuse
        self.max_retries = max_retries
        self.on_log = on_log or (lambda *_: None)
        self._session: Optional[aiohttp.ClientSession] = None
        self._timeout = aiohttp.ClientTimeout(
            total=timeout_total, sock_connect=30, sock_read=300
        )

    # -- plumbing ---------------------------------------------------------- #
    @property
    def authorized(self) -> bool:
        return self.credentials is not None

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={
                    "User-Agent": USER_AGENT,
                    # aiohttp would otherwise advertise "gzip, deflate, br, zstd"
                    # on every request. With auto_decompress off (needed by the
                    # byte pump) that left metadata replies compressed, and the
                    # json/text helpers then died with
                    # "UnicodeDecodeError: 0x8b in position 1". Asking for
                    # identity keeps metadata plain while Range downloads stay
                    # byte-exact.
                    "Accept-Encoding": "identity",
                },
                auto_decompress=False,
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def whoami(self) -> str:
        """Confirm the credentials actually work; returns the account identity.

        IMPORTANT: the identity is read from the **Drive API**, not from
        oauth2/v3/userinfo. A token minted for `drive.readonly` (or `drive`) has
        no `openid`/`email` scope, and Google answers userinfo with
        `401 Invalid Credentials` even though the token is perfectly valid for
        Drive. Using userinfo as the health check made working credentials look
        broken ("Token rejected (HTTP 401)") — exactly the bug this replaced.

        About.get works with drive.readonly and returns the signed-in user's
        emailAddress, so it doubles as a real Drive permission check.
        """
        if not self.credentials:
            raise DriveError("No Google credentials configured.")
        sess = await self.session()
        headers = await self._auth_headers()
        fields = "user(displayName,emailAddress,me),storageQuota(limit,usage)"

        async with sess.get(
            f"{DRIVE_API}/about", params={"fields": fields}, headers=headers
        ) as r:
            if r.status == 200:
                data = await read_json(r)
                user = data.get("user", {}) or {}
                email = user.get("emailAddress")
                if email:
                    return email
                # No email in the payload (rare) but Drive answered: still fine.
                return user.get("displayName") or self.credentials.email
            body = await read_text(r)
            low = body.lower()

        if r.status == 401:
            raise DriveError(
                "Google rejected the access token (HTTP 401 Invalid Credentials). "
                "The token refreshed from your refresh token is not accepted — "
                "usually the GOOGLE_REFRESH_TOKEN / GOOGLE_CLIENT_ID pair is "
                "mismatched, or the token was revoked.\n\n"
                "Fix: mint a NEW refresh token with the same client you put in "
                "GOOGLE_CLIENT_ID (Playground → ⚙ Use your own OAuth credentials), "
                "update all three env vars, redeploy.\n"
                "Or run: python diagnose_credentials.py"
            )
        if r.status == 403 and ("quota" in low or "storagequota" in low):
            raise DriveError(
                "Drive answered but this project is over quota (HTTP 403). Check "
                "the Google Cloud project's Drive API quota."
            )
        if r.status == 403:
            raise DriveError(
                f"Drive refused the request (HTTP 403). The token may not include "
                f"a Drive scope, or the Drive API is not enabled in this project.\n"
                f"{body[:300]}"
            )
        raise DriveError(f"Drive API rejected the credentials (HTTP {r.status}).")

    async def _auth_headers(self) -> dict[str, str]:
        if not self.credentials:
            return {}
        return {"Authorization": f"Bearer {await self.credentials.token()}"}

    # -- metadata ---------------------------------------------------------- #
    async def get_file(self, file_id: str) -> DriveFile:
        """Metadata for a single file. Works authorized or (for public files)
        with just an API key."""
        fields = "id,name,size,mimeType,modifiedTime,trashed"
        if self.authorized:
            url = f"{DRIVE_API}/files/{file_id}"
            params = {"fields": fields, "supportsAllDrives": "true"}
            headers = await self._auth_headers()
        elif self.api_key:
            url = f"{DRIVE_API}/files/{file_id}"
            params = {"fields": fields, "key": self.api_key, "supportsAllDrives": "true"}
            headers = {}
        else:
            return await self._get_file_scraped(file_id)

        sess = await self.session()
        async with sess.get(url, params=params, headers=headers) as r:
            if r.status == 404:
                raise GoogleAccessError(
                    "Google returned 404: the file doesn't exist, or it is private "
                    "and not visible to this bot's Google account."
                )
            if r.status == 403:
                raise GoogleAccessError(
                    "Google returned 403 (permission denied). The file is restricted "
                    "to specific people. Authorize the bot as an account that has "
                    "access, or ask the owner to share it."
                )
            if r.status != 200:
                raise DriveError(f"Drive metadata request failed (HTTP {r.status}).")
            data = await read_json(r)

        if data.get("trashed"):
            raise GoogleAccessError("That file is in the Trash.")
        size = data.get("size")
        return DriveFile(
            id=data["id"],
            name=data.get("name") or data["id"],
            size=int(size) if size not in (None, "") else None,
            mime_type=data.get("mimeType") or "application/octet-stream",
            modified=data.get("modifiedTime"),
        )

    async def _get_file_scraped(self, file_id: str) -> DriveFile:
        """No credentials and no API key: sniff the public view page."""
        sess = await self.session()
        url = f"https://drive.google.com/file/d/{file_id}/view"
        async with sess.get(url) as r:
            text = await read_text(r)
        if "Sign in" in text and "accounts.google.com" in text:
            raise GoogleAccessError(
                "This file is private. The bot has no Google credentials "
                "configured, so it can only fetch files shared with 'anyone "
                "with the link'. Use /auth to check, or configure credentials."
            )
        title = re.search(r"<title>(.*?)</title>", text, re.S | re.I)
        name = sanitize_filename(html.unescape(title.group(1) if title else file_id))
        name = re.sub(r"\s*-\s*Google\s*Drive\s*$", "", name, flags=re.I) or file_id
        return DriveFile(id=file_id, name=name, size=None, mime_type="")

    async def _list_children(self, folder_id: str) -> AsyncIterator[dict]:
        """Yield every child of one folder, following Drive's page tokens."""
        if not (self.authorized or self.api_key):
            raise GoogleAccessError(
                "Listing folders requires Google credentials or an API key. "
                "Run /auth to see the current mode."
            )
        sess = await self.session()
        headers = await self._auth_headers()
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": (
                "nextPageToken, files(id,name,size,mimeType,modifiedTime,"
                "shortcutDetails(targetId,targetMimeType))"
            ),
            "pageSize": "1000",
            "orderBy": "folder,name_natural",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if not self.authorized and self.api_key:
            params["key"] = self.api_key

        page_token: Optional[str] = None
        while True:
            if page_token:
                params["pageToken"] = page_token
            async with sess.get(
                f"{DRIVE_API}/files", params=params, headers=headers
            ) as r:
                if r.status in (401, 403, 404):
                    raise GoogleAccessError(
                        f"Cannot list that folder (HTTP {r.status}). Share it with "
                        "the bot's Google account, or open it while authorized."
                    )
                if r.status != 200:
                    raise DriveError(f"Folder listing failed (HTTP {r.status}).")
                data = await read_json(r)
            for item in data.get("files", []):
                yield item
            page_token = data.get("nextPageToken")
            if not page_token:
                return

    async def list_folder(self, folder_id: str, max_files: int = 30) -> list[DriveFile]:
        """Flat, non-recursive listing in natural name order."""
        items = [
            i
            async for i in self._list_children(folder_id)
            if i.get("mimeType") != FOLDER_MIME
        ]
        items.sort(key=lambda i: natural_key(i.get("name")))
        return [self._to_drive_file(i) for i in items[:max_files]]

    async def iter_folder(
        self,
        folder_id: str,
        *,
        recursive: bool = True,
        max_files: int = 500,
        max_depth: int = 12,
        order: str = "deep",
    ) -> AsyncIterator[DriveFile]:
        """Walk a folder and yield files **in course order**.

        order="deep" (default) is depth-first: Module 1 and everything inside it
        is emitted completely before Module 2 starts, so the Telegram chat reads
        like the syllabus. order="breadth" walks level by level.

        Within a folder, entries are sorted "folders first, then natural name",
        so `Lesson 2` precedes `Lesson 10` instead of following it.
        """
        if not (self.authorized or self.api_key):
            raise GoogleAccessError(
                "Folder scanning needs Google credentials or an API key. "
                "Public single-file links still work without them."
            )

        visited: set[str] = set()
        emitted = 0

        def sort_items(items: list[dict]) -> list[dict]:
            return sorted(
                items,
                key=lambda i: (
                    0 if i.get("mimeType") == FOLDER_MIME else 1,
                    natural_key(i.get("name")),
                ),
            )

        async def resolve_shortcut(item: dict, prefix: str) -> Optional[DriveFile]:
            target = (item.get("shortcutDetails") or {}).get("targetId")
            if not target:
                return None
            try:
                target_file = await self.get_file(target)
            except DriveError as exc:
                self.on_log(f"skipping shortcut {item.get('name')}: {exc}")
                return None
            target_file.rel_path = prefix + target_file.name
            return target_file

        def to_file(item: dict, prefix: str) -> DriveFile:
            size_raw = item.get("size")
            name = item.get("name") or item["id"]
            return DriveFile(
                id=item["id"],
                name=name,
                size=int(size_raw) if size_raw not in (None, "") else None,
                mime_type=item.get("mimeType") or "application/octet-stream",
                modified=item.get("modifiedTime"),
                rel_path=prefix + name,
            )

        if order == "breadth":
            queue: list[tuple[str, str, int]] = [(folder_id, "", 0)]
            while queue and emitted < max_files:
                current, prefix, depth = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                items = [i async for i in self._list_children(current)]
                for item in sort_items(items):
                    if emitted >= max_files:
                        return
                    mime = item.get("mimeType") or ""
                    if mime == FOLDER_MIME:
                        if recursive and depth < max_depth:
                            queue.append((item["id"], f"{prefix}{item.get('name')}/", depth + 1))
                        continue
                    if mime == SHORTCUT_MIME:
                        resolved = await resolve_shortcut(item, prefix)
                        if resolved:
                            yield resolved
                            emitted += 1
                        continue
                    yield to_file(item, prefix)
                    emitted += 1
            return

        # depth-first: the sequence the user actually expects for a course
        async def walk(fid: str, prefix: str, depth: int):
            nonlocal emitted
            if emitted >= max_files or fid in visited:
                return
            visited.add(fid)
            items = [i async for i in self._list_children(fid)]
            for item in sort_items(items):
                if emitted >= max_files:
                    return
                mime = item.get("mimeType") or ""
                name = item.get("name") or item["id"]
                if mime == FOLDER_MIME:
                    if recursive and depth < max_depth:
                        async for sub in walk(item["id"], f"{prefix}{name}/", depth + 1):
                            yield sub
                    continue
                if mime == SHORTCUT_MIME:
                    resolved = await resolve_shortcut(item, prefix)
                    if resolved:
                        yield resolved
                        emitted += 1
                    continue
                yield to_file(item, prefix)
                emitted += 1

        async for f in walk(folder_id, "", 0):
            yield f

    @staticmethod
    def _to_drive_file(item: dict) -> DriveFile:
        size_raw = item.get("size")
        return DriveFile(
            id=item["id"],
            name=item.get("name") or item["id"],
            size=int(size_raw) if size_raw not in (None, "") else None,
            mime_type=item.get("mimeType") or "application/octet-stream",
            modified=item.get("modifiedTime"),
        )

    # -- download ---------------------------------------------------------- #
    async def download(
        self,
        target: "DriveFile | str",
        dest: Path,
        on_progress: Optional[ProgressCb] = None,
        cancel: Optional[asyncio.Event] = None,
        max_bytes: Optional[int] = None,
    ) -> Path:
        """Stream a file to `dest` in 1 MiB chunks, resuming partial downloads.

        RAM usage is flat (~1 MiB + buffers) no matter how big the file is, and
        an interrupted transfer continues from the byte it stopped at instead of
        restarting.
        """
        info = target if isinstance(target, DriveFile) else await self.get_file(target)
        if max_bytes and info.size and info.size > max_bytes:
            raise FileTooLargeError(
                f"{human_bytes(info.size)} exceeds the configured limit of "
                f"{human_bytes(max_bytes)}."
            )

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        cancel = cancel or asyncio.Event()

        if info.is_google_native:
            return await self._export_google_doc(info, dest, on_progress, cancel)

        url, headers = await self._media_request(info)
        return await self._stream_resumable(
            url, headers, dest, info, on_progress, cancel
        )

    async def _media_request(self, info: DriveFile) -> tuple[str, dict[str, str]]:
        """Pick the best download URL for this file + our credentials."""
        if self.authorized:
            headers = await self._auth_headers()
            url = f"{DRIVE_API}/files/{info.id}?alt=media&supportsAllDrives=true"
            if self.acknowledge_abuse:
                url += "&acknowledgeAbuse=true"
            return url, headers
        if self.api_key:
            return (
                f"{DRIVE_API}/files/{info.id}?alt=media&key={self.api_key}"
                "&supportsAllDrives=true",
                {},
            )
        # Anonymous fallback. Per Google's current behaviour (verified Oct 2026):
        #   * drive.google.com/uc?... 303-redirects to drive.usercontent.google.com
        #     AND DROPS the confirm parameter, so big files return the
        #     "can't scan for viruses" HTML page instead of bytes.
        #   * The usercontent host honours confirm=t (the same value the
        #     "Download anyway" button submits), which is needed for files
        #     roughly >100 MB -- i.e. every course video.
        # So go straight to the usercontent host with confirm=t.
        return f"{USERCONTENT_DOWNLOAD}?id={info.id}&export=download&confirm=t", {}

    async def _export_google_doc(
        self,
        info: DriveFile,
        dest: Path,
        on_progress: Optional[ProgressCb],
        cancel: asyncio.Event,
    ) -> Path:
        mime, _ext = info.export_target()
        if self.authorized:
            headers = await self._auth_headers()
            url = f"{DRIVE_API}/files/{info.id}/export?mimeType={mime}"
        else:
            fmt = "pdf" if mime == "application/pdf" else "xlsx"
            kind = (
                "spreadsheets"
                if fmt == "xlsx"
                else ("presentation" if "presentation" in info.mime_type else "document")
            )
            url = f"https://docs.google.com/{kind}/d/{info.id}/export?format={fmt}"
            headers = {}
        return await self._stream_resumable(url, headers, dest, info, on_progress, cancel)

    # -- the actual byte pump --------------------------------------------- #
    async def _stream_resumable(
        self,
        url: str,
        headers: dict[str, str],
        dest: Path,
        info: DriveFile,
        on_progress: Optional[ProgressCb],
        cancel: asyncio.Event,
    ) -> Path:
        part = dest.with_name(dest.name + ".part")
        attempt = 0
        last_error: Optional[Exception] = None

        while attempt <= self.max_retries:
            if cancel.is_set():
                raise CancelledByUser("Cancelled.")
            attempt += 1
            done_before = part.stat().st_size if part.exists() else 0
            req_headers = dict(headers)
            if done_before:
                req_headers["Range"] = f"bytes={done_before}-"

            try:
                sess = await self.session()
                async with sess.get(url, headers=req_headers, allow_redirects=True) as r:
                    if r.status == 416:  # already complete
                        break
                    if r.status == 403:
                        body = await read_text(r)
                        low = body.lower()
                        if "downloadquotaexceeded" in low or "quota" in low:
                            raise QuotaExceededError(QUOTA_HELP)
                        raise GoogleAccessError(self._explain_http(403, info))
                    if r.status in (401, 404, 410):
                        raise GoogleAccessError(self._explain_http(r.status, info))
                    if r.status == 429 or r.status >= 500:
                        raise aiohttp.ClientError(f"HTTP {r.status}")

                    ctype = (r.headers.get("Content-Type") or "").lower()
                    if ctype.startswith("text/html"):
                        # Either the "can't scan this file" confirm page, a
                        # sign-in wall, or an error page. Try the handshake once.
                        body = await read_text(r)
                        token = _find_confirm_token(body)
                        if token and "confirm" not in url:
                            url = _with_confirm(url, token)
                            attempt -= 1
                            continue
                        raise GoogleAccessError(self._explain_html(body, info))
                    if r.status not in (200, 206):
                        raise aiohttp.ClientError(f"HTTP {r.status}")

                    total = _total_from_response(r, done_before, info.size)
                    mode = "ab" if (r.status == 206 and done_before) else "wb"
                    done = done_before if mode == "ab" else 0
                    if on_progress:
                        await on_progress(done, total)

                    with open(part, mode) as fh:
                        async for chunk in r.content.iter_chunked(CHUNK_SIZE):
                            if cancel.is_set():
                                fh.flush()
                                raise CancelledByUser("Cancelled.")
                            fh.write(chunk)
                            done += len(chunk)
                            if on_progress:
                                await on_progress(done, total)
                break  # success
            except CancelledByUser:
                raise
            except GoogleAccessError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                last_error = exc
                self.on_log(f"download retry {attempt}/{self.max_retries}: {exc!r}")
                if attempt > self.max_retries:
                    break
                await asyncio.sleep(min(2 ** attempt, 30))

        if not part.exists():
            raise DriveError(
                f"Download failed after {self.max_retries} attempts: {last_error!r}"
            )

        final_size = part.stat().st_size
        if info.size and final_size != info.size and not info.is_google_native:
            # Don't hand back a truncated file.
            raise DriveError(
                f"Size mismatch: expected {human_bytes(info.size)}, "
                f"got {human_bytes(final_size)}. Kept {part.name} for a retry."
            )
        if dest.exists():
            dest.unlink()
        part.replace(dest)
        if on_progress:
            await on_progress(final_size, final_size)
        return dest

    # -- Google's internal stream endpoint (separate video/audio tracks) ---- #
    async def get_stream_parts(self, file_id: str) -> tuple[Optional[str], Optional[str]]:
        """Pull the direct `videoplayback` video/audio URLs out of the viewer page.

        This is the mechanism shown in the "view-only" download tutorials: the
        Drive preview player streams from drive.usercontent.google.com with
        `itag`/`mime=video/...` for the video track and `mime=audio/...` for the
        audio track, each accepting HTTP Range requests.

        Reality check, measured rather than assumed:
          * It returns the ORIGINAL encoded tracks (no re-encode, no quality loss).
          * Audio is a separate URL, so the two must be muxed (see media_mux.py).
          * The URLs are short-lived and bound to the anonymous viewer, so this
            only works for public / link-shared files, not private ones.
        """
        sess = await self.session()
        headers = {"User-Agent": USER_AGENT}
        try:
            async with sess.get(
                f"https://drive.google.com/file/d/{file_id}/view", headers=headers
            ) as r:
                page = await read_text(r)
        except Exception as exc:  # noqa: BLE001
            self.on_log(f"stream parts: page fetch failed: {exc!r}")
            return None, None

        if "accounts.google.com" in page and "Sign in" in page:
            self.on_log("stream parts: page requires sign-in (file is private)")
            return None, None

        # Unescape the JS string literals so the URLs are usable directly.
        page = page.replace("\\u003d", "=").replace("\\u0026", "&").replace("\\/", "/")
        urls = re.findall(r'https://[^"\'\s]+?videoplayback[^"\'\s]*', page)

        def _is(url: str, kind: str) -> bool:
            return f"mime={kind}" in url or f"mime%3D{kind}" in url

        def _itag(url: str) -> Optional[int]:
            m = re.search(r"[?&]itag=(\d+)", url)
            return int(m.group(1)) if m else None

        # The manifest lists several renditions per track. Picking blindly gives
        # you whatever appears first — often a 360p video track. Prefer an MP4
        # rendition (H.264/AAC muxes into .mp4 losslessly; VP9/Opus from the
        # WebM renditions would need a Matroska container), and among those take
        # the highest itag, which is Google's ordering from low to high.
        video_candidates = [u for u in urls if _is(u, "video")]
        audio_candidates = [u for u in urls if _is(u, "audio")]

        def _rank(candidates: list[str]) -> Optional[str]:
            if not candidates:
                return None
            mp4 = [u for u in candidates if "mp4" in u]
            pool = mp4 or candidates
            return max(pool, key=lambda u: _itag(u) or 0)

        video = _rank(video_candidates)
        audio = _rank(audio_candidates)
        self.on_log(
            f"stream parts: {len(urls)} candidate urls "
            f"(video {len(video_candidates)}, audio {len(audio_candidates)}), "
            f"itag v={_itag(video) if video else None} a={_itag(audio) if audio else None}"
        )
        return video, audio

    # -- quota workaround -------------------------------------------------- #
    async def copy_to_my_drive(self, file_id: str, name: Optional[str] = None) -> DriveFile:
        """Make a private copy owned by the bot's account.

        Why this works: Google throttles egress per *file object*. The copy is a
        brand-new object with its own quota, so it downloads immediately. This is
        the documented-behaviour workaround the DataHoarder thread describes,
        done through the official API instead of the web UI.

        Requires the full `drive` scope (readonly cannot create files).
        """
        if not self.credentials or not getattr(self.credentials, "can_write", False):
            raise DriveError(
                "Making a copy needs full Drive access, but this bot only has "
                "read-only scope. Re-run authorization with the full scope "
                "(DRIVE_SCOPE=full) to enable /quotafix."
            )
        sess = await self.session()
        headers = await self._auth_headers()
        body = {"name": name or f"quota-fix-{file_id[:12]}"}
        async with sess.post(
            f"{DRIVE_API}/files/{file_id}/copy",
            params={"fields": "id,name,size,mimeType", "supportsAllDrives": "true"},
            json=body,
            headers=headers,
        ) as r:
            if r.status in (401, 403):
                detail = await read_text(r)
                if "storageQuota" in detail or "storage quota" in detail.lower():
                    raise DriveError(
                        "Your Google account has no free storage left, so a copy "
                        "cannot be made. Free up space (or delete the last copy) "
                        "and retry — the bot deletes its copies automatically."
                    )
                raise GoogleAccessError(
                    "Google refused to copy this file. The account needs at least "
                    "Viewer access, and the file must not be copy-protected by the "
                    "owner (Drive can disable copying)."
                )
            if r.status != 200:
                raise DriveError(f"Copy failed (HTTP {r.status}).")
            data = await read_json(r)
        size = data.get("size")
        return DriveFile(
            id=data["id"],
            name=data.get("name") or name or file_id,
            size=int(size) if size not in (None, "") else None,
            mime_type=data.get("mimeType") or "application/octet-stream",
        )

    async def delete_file(self, file_id: str) -> None:
        """Permanently delete a file the bot created (used to clean up copies)."""
        if not self.credentials or not getattr(self.credentials, "can_write", False):
            return
        sess = await self.session()
        headers = await self._auth_headers()
        async with sess.delete(
            f"{DRIVE_API}/files/{file_id}", headers=headers
        ) as r:
            if r.status not in (200, 204, 404):
                self.on_log(f"cleanup delete failed for {file_id}: HTTP {r.status}")

    async def download_via_copy(
        self,
        file_id: str,
        dest: Path,
        on_progress: Optional[ProgressCb] = None,
        cancel: Optional[asyncio.Event] = None,
        max_bytes: Optional[int] = None,
        delete_after: bool = True,
    ) -> tuple[Path, DriveFile]:
        """Quota-safe download: copy -> download the copy -> delete the copy."""
        info = await self.get_file(file_id)
        copy = await self.copy_to_my_drive(file_id, name=info.download_name)
        self.on_log(f"quota fix: made copy {copy.id} of {file_id}")
        try:
            path = await self.download(
                copy, dest, on_progress=on_progress, cancel=cancel, max_bytes=max_bytes
            )
        finally:
            if delete_after:
                await self.delete_file(copy.id)
        return path, copy

    # -- friendly diagnostics --------------------------------------------- #
    @staticmethod
    def _explain_http(status: int, info: DriveFile) -> str:
        if status == 404:
            return (
                "Google says 404 — the file is gone, or it's private and invisible "
                "to this bot's Google account."
            )
        if status in (401, 403):
            if info.mime_type and "google-apps" not in info.mime_type and not info.size:
                return "Permission denied (403) for this file."
            return (
                "Permission denied (403). This is a restricted file: only the people "
                "it's shared with can read it. Authorize the bot with an account that "
                "has access (see /auth), or ask the owner to grant access — access "
                "control is not something the bot can or should bypass."
            )
        return f"Google refused the download (HTTP {status})."

    @staticmethod
    def _explain_html(body: str, info: DriveFile) -> str:
        low = body.lower()
        if "accounts.google.com" in low or "sign in" in low:
            return (
                "This file is private. Google served a sign-in page instead of the "
                "bytes. Configure bot credentials for an account with access."
            )
        if "download quota" in low or (
            "quota" in low and "exceeded" in low
        ) or "too many users have viewed or downloaded" in low:
            return QUOTA_HELP
        if "cannot scan" in low or "too large to scan" in low or "virus" in low:
            return (
                "Google refused to serve an unscannable file anonymously. Retry with "
                "authorized credentials, or set DRIVE_ACKNOWLEDGE_ABUSE=true if this "
                "file is yours."
            )
        return "Google returned an HTML page instead of the file — usually a sign-in wall or quota block."


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _find_confirm_token(body: str) -> Optional[str]:
    """Pull the download confirmation token out of Google's interstitial page.

    This is the standard public large-file handshake (Google can't virus-scan
    huge files), not an authentication bypass.
    """
    patterns = [
        r"confirm=([0-9A-Za-z_\-]+)",
        r'name="confirm"\s+value="([^"]+)"',
        r'"downloadToken":"([^"]+)"',
        r"uuid=([0-9A-Za-z_\-]{10,})",
    ]
    for pat in patterns:
        m = re.search(pat, body)
        if m:
            return m.group(1)
    return None


def _with_confirm(url: str, token: str) -> str:
    """Re-request with the confirmation token, on the host that honours it.

    drive.google.com/uc drops `confirm` on redirect, so a retry there loops
    forever on the virus-scan page. Drive's usercontent endpoint honours it.
    """
    if "usercontent.google.com" in url or "localhost" in url or "127.0.0.1" in url:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}confirm={token}"
    match = re.search(r"[?&]id=([A-Za-z0-9_-]+)", url)
    file_id = match.group(1) if match else ""
    if file_id:
        return f"{USERCONTENT_DOWNLOAD}?id={file_id}&export=download&confirm={token}"
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}confirm={token}"


def _total_from_response(
    response: aiohttp.ClientResponse, done_before: int, expected: Optional[int]
) -> Optional[int]:
    cr = response.headers.get("Content-Range")
    if cr and "/" in cr:
        tail = cr.rsplit("/", 1)[1].strip()
        if tail.isdigit():
            return int(tail)
    cl = response.headers.get("Content-Length")
    if cl and cl.isdigit():
        return int(cl) + (done_before if response.status == 206 else 0)
    return expected


def human_bytes(num: Optional[float]) -> str:
    if num is None:
        return "?"
    num = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.2f} {unit}"
        num /= 1024
    return f"{num:.2f} TB"


def format_gb(num_bytes: Optional[float]) -> str:
    if not num_bytes:
        return "unlimited"
    return f"{num_bytes / 1024**3:.1f} GB"


def human_rate(bytes_per_sec: float) -> str:
    return f"{human_bytes(bytes_per_sec)}/s"


def eta_text(done: int, total: Optional[int], elapsed: float) -> str:
    if not total or elapsed <= 0 or done <= 0:
        return "—"
    rate = done / elapsed
    if rate <= 0:
        return "—"
    remaining = max(total - done, 0) / rate
    if remaining < 60:
        return f"{int(remaining)}s"
    if remaining < 3600:
        return f"{int(remaining // 60)}m {int(remaining % 60)}s"
    return f"{int(remaining // 3600)}h {int((remaining % 3600) // 60)}m"


def progress_bar(fraction: float, width: int = 14) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = int(round(fraction * width))
    return "▰" * filled + "▱" * (width - filled)


def iter_file_parts(path: Path, part_size: int) -> Iterable[tuple[Path, int, int]]:
    """Split a huge file into byte-range parts on disk (for uploaders capped at
    a fixed size). Yields (part_path, index, count) after writing each part."""
    total = path.stat().st_size
    count = max(1, -(-total // part_size))
    if count == 1:
        yield path, 1, 1
        return
    with open(path, "rb") as src:
        for idx in range(1, count + 1):
            part = path.with_name(f"{path.name}.part{idx:03d}")
            remaining = part_size
            with open(part, "wb") as dst:
                while remaining > 0:
                    buf = src.read(min(CHUNK_SIZE, remaining))
                    if not buf:
                        break
                    dst.write(buf)
                    remaining -= len(buf)
            yield part, idx, count
            try:
                part.unlink()
            except OSError:
                pass
