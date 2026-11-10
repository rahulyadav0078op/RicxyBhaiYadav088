"""
test_core.py — offline tests for link parsing and the downloader's behaviour.
Runs with plain pytest, no network, no Telegram, no Google credentials.

    pip install pytest aiohttp
    pytest -q
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import gdrive_core as core
from gdrive_core import (
    DriveClient,
    DriveError,
    DriveFile,
    GoogleAccessError,
    eta_text,
    human_bytes,
    iter_file_parts,
    parse_drive_links,
    progress_bar,
    sanitize_filename,
    _find_confirm_token,
)


# --------------------------------------------------------------------------- #
# Link parsing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url,kind,file_id",
    [
        (
            "https://drive.google.com/file/d/1A2b3C4d5E6f7G8h9I0jK/view?usp=sharing",
            "file",
            "1A2b3C4d5E6f7G8h9I0jK",
        ),
        (
            "https://drive.google.com/open?id=1A2b3C4d5E6f7G8h9I0jK",
            "file",
            "1A2b3C4d5E6f7G8h9I0jK",
        ),
        (
            "https://drive.google.com/uc?export=download&id=1A2b3C4d5E6f7G8h9I0jK",
            "file",
            "1A2b3C4d5E6f7G8h9I0jK",
        ),
        (
            "https://docs.google.com/spreadsheets/d/1A2b3C4d5E6f7G8h9I0jK/edit#gid=0",
            "file",
            "1A2b3C4d5E6f7G8h9I0jK",
        ),
        (
            "https://docs.google.com/document/d/1A2b3C4d5E6f7G8h9I0jK/edit",
            "file",
            "1A2b3C4d5E6f7G8h9I0jK",
        ),
        (
            "https://drive.google.com/drive/folders/1A2b3C4d5E6f7G8h9I0jK?usp=drive_link",
            "folder",
            "1A2b3C4d5E6f7G8h9I0jK",
        ),
        (
            "https://drive.google.com/drive/u/2/folders/1A2b3C4d5E6f7G8h9I0jK",
            "folder",
            "1A2b3C4d5E6f7G8h9I0jK",
        ),
    ],
)
def test_parse_variants(url, kind, file_id):
    refs = parse_drive_links(url)
    assert refs == [core.DriveRef(kind, file_id)]


def test_parse_multiple_dedup_and_limit():
    text = (
        "a https://drive.google.com/file/d/1A2b3C4d5E6f7G8h9I0jK/view "
        "b https://drive.google.com/file/d/1A2b3C4d5E6f7G8h9I0jK/view "
        "c https://drive.google.com/file/d/1Z2b3C4d5E6f7G8h9I0jK/view"
    )
    refs = parse_drive_links(text)
    assert [r.file_id for r in refs] == ["1A2b3C4d5E6f7G8h9I0jK", "1Z2b3C4d5E6f7G8h9I0jK"]


def test_parse_ignores_non_drive_and_short_ids():
    assert parse_drive_links("https://example.com/file/d/abc/view") == []
    assert parse_drive_links("https://drive.google.com/file/d/short/view") == []
    assert parse_drive_links("hello there") == []


# --------------------------------------------------------------------------- #
# Filenames / formatting
# --------------------------------------------------------------------------- #
def test_sanitize_filename():
    assert sanitize_filename('a/b\\c:d?.zip') == "a_b_c_d_.zip"
    assert sanitize_filename("  ...report.pdf  ") == "report.pdf"
    long = "x" * 300 + ".pdf"
    assert len(sanitize_filename(long)) == 120


def test_download_name_for_google_docs():
    doc = DriveFile("id", "My Doc", None, "application/vnd.google-apps.document")
    assert doc.is_google_native
    assert doc.download_name == "My Doc.pdf"
    sheet = DriveFile("id", "Sheet", None, "application/vnd.google-apps.spreadsheet")
    assert sheet.download_name.endswith(".xlsx")
    plain = DriveFile("id", "notes", 10, "text/plain")
    assert plain.download_name == "notes.txt"


def test_human_bytes_and_bar():
    assert human_bytes(0) == "0 B"
    assert human_bytes(1536) == "1.50 KB"
    assert human_bytes(None) == "?"
    assert progress_bar(0.0).count("▰") == 0
    assert progress_bar(1.0).count("▰") == 14
    assert eta_text(0, None, 1) == "—"


def test_confirm_token_extraction():
    body = '<a href="/uc?export=download&amp;confirm=AbC123_x-y&amp;id=1Abcdefghij">'
    assert _find_confirm_token(body) == "AbC123_x-y"
    assert _find_confirm_token('<input name="confirm" value="ZZ99">') == "ZZ99"
    assert _find_confirm_token("no token here") is None


# --------------------------------------------------------------------------- #
# Downloader against a local fake server
# --------------------------------------------------------------------------- #
class FakeDrive:
    """Minimal aiohttp app imitating Drive's media endpoints."""

    def __init__(self, payload: bytes, *, require_confirm: bool = False, signin=False):
        self.payload = payload
        self.require_confirm = require_confirm
        self.signin = signin
        self.range_requests: list[str] = []
        self.requests = 0

    async def handle(self, request):
        from aiohttp import web

        self.requests += 1
        if self.signin:
            return web.Response(
                text="<html><title>Sign in - Google Accounts</title>"
                "https://accounts.google.com/ServiceLogin</html>",
                content_type="text/html",
            )
        if self.require_confirm and "confirm=" not in str(request.url):
            return web.Response(
                text='<html>Google Drive - Virus scan warning '
                '<a href="/uc?export=download&confirm=tAbC123&id=1">Download anyway</a></html>',
                content_type="text/html",
            )
        rng = request.headers.get("Range")
        total = len(self.payload)
        if rng:
            self.range_requests.append(rng)
            start = int(rng.split("=")[1].split("-")[0])
            chunk = self.payload[start:]
            return web.Response(
                body=chunk,
                status=206,
                headers={
                    "Content-Range": f"bytes {start}-{total - 1}/{total}",
                    "Content-Length": str(len(chunk)),
                    "Content-Type": "application/octet-stream",
                },
            )
        return web.Response(
            body=self.payload,
            headers={
                "Content-Length": str(total),
                "Content-Type": "application/octet-stream",
            },
        )


def _run(coro):
    return asyncio.run(coro)


def _serve(fake: FakeDrive):
    from aiohttp import web

    app = web.Application()
    app.router.add_route("GET", "/{tail:.*}", fake.handle)
    return app


@pytest.fixture()
def server_factory():
    from aiohttp import web

    runners = []

    async def _start(fake: FakeDrive):
        runner = web.AppRunner(_serve(fake))
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        runners.append(runner)
        return f"http://127.0.0.1:{port}"

    yield _start

    async def _stop_all():
        for r in runners:
            await r.cleanup()

    _run(_stop_all())


def test_download_plain(server_factory, tmp_path):
    payload = bytes(range(256)) * 5000  # ~1.25 MB
    fake = FakeDrive(payload)

    async def go():
        base = await server_factory(fake)
        client = DriveClient()
        client._media_request = lambda info: _ret(f"{base}/uc?export=download&id=x", {})
        seen = []

        async def prog(done, total):
            seen.append((done, total))

        dest = tmp_path / "out.bin"
        await client.download(
            DriveFile("1A2b3C4d5E6f7G8h9I0jK", "out.bin", len(payload), "application/octet-stream"),
            dest,
            on_progress=prog,
        )
        await client.close()
        assert dest.read_bytes() == payload
        assert seen[-1] == (len(payload), len(payload))

    _run(go())


def test_download_resumes_from_partial(server_factory, tmp_path):
    payload = b"ABCDEFGHIJ" * 1000
    fake = FakeDrive(payload)

    async def go():
        base = await server_factory(fake)
        client = DriveClient()
        client._media_request = lambda info: _ret(f"{base}/uc?export=download&id=x", {})
        dest = tmp_path / "resume.bin"
        half = len(payload) // 2
        (tmp_path / "resume.bin.part").write_bytes(payload[:half])
        await client.download(
            DriveFile("1A2b3C4d5E6f7G8h9I0jK", "resume.bin", len(payload), ""), dest
        )
        await client.close()
        assert fake.range_requests == [f"bytes={half}-"]
        assert dest.read_bytes() == payload

    _run(go())


def test_download_confirm_handshake(server_factory, tmp_path, monkeypatch):
    payload = b"Z" * 4096
    fake = FakeDrive(payload, require_confirm=True)

    async def go():
        base = await server_factory(fake)
        monkeypatch.setattr(core, "USERCONTENT_DOWNLOAD", f"{base}/download")
        client = DriveClient()
        client._media_request = lambda info: _ret(
            f"{base}/download?id=x&export=download", {}
        )
        dest = tmp_path / "conf.bin"
        await client.download(DriveFile("1A2b3C4d5E6f7G8h9I0jK", "conf.bin", None, ""), dest)
        await client.close()
        assert dest.read_bytes() == payload

    _run(go())


def test_private_file_raises_clear_error(server_factory, tmp_path):
    fake = FakeDrive(b"", signin=True)

    async def go():
        base = await server_factory(fake)
        client = DriveClient()
        client._media_request = lambda info: _ret(f"{base}/uc?export=download&id=x", {})
        with pytest.raises(GoogleAccessError) as err:
            await client.download(
                DriveFile("1A2b3C4d5E6f7G8h9I0jK", "x", None, ""), tmp_path / "x.bin"
            )
        await client.close()
        assert "private" in str(err.value).lower()

    _run(go())


def test_size_mismatch_is_rejected(server_factory, tmp_path):
    fake = FakeDrive(b"short")

    async def go():
        base = await server_factory(fake)
        client = DriveClient(max_retries=0)
        client._media_request = lambda info: _ret(f"{base}/uc?export=download&id=x", {})
        with pytest.raises(core.DriveError):
            await client.download(
                DriveFile("1A2b3C4d5E6f7G8h9I0jK", "x", 999999, ""), tmp_path / "x.bin"
            )
        await client.close()
        assert (tmp_path / "x.bin.part").exists()

    _run(go())


def test_cancel_stops_download(server_factory, tmp_path):
    payload = b"Q" * (2 * 1024 * 1024)
    fake = FakeDrive(payload)

    async def go():
        base = await server_factory(fake)
        client = DriveClient()
        client._media_request = lambda info: _ret(f"{base}/uc?export=download&id=x", {})
        cancel = asyncio.Event()

        async def prog(done, total):
            cancel.set()

        with pytest.raises(core.CancelledByUser):
            await client.download(
                DriveFile("1A2b3C4d5E6f7G8h9I0jK", "x", len(payload), ""),
                tmp_path / "x.bin",
                on_progress=prog,
                cancel=cancel,
            )
        await client.close()

    _run(go())


def test_split_parts_reassemble(tmp_path):
    data = hashlib.sha256(b"x").digest() * 40000  # 1.28 MB
    src = tmp_path / "big.bin"
    src.write_bytes(data)
    part_size = 300_000
    captured = []
    for part, idx, count in iter_file_parts(src, part_size):
        captured.append((idx, count, part.read_bytes()))
    joined = b"".join(c[2] for c in captured)
    assert joined == data
    assert captured[0][1] == -(-len(data) // part_size)
    assert not any(p.name.startswith("big.bin.part") for p in tmp_path.iterdir())


def test_max_bytes_guard(tmp_path):
    async def go():
        client = DriveClient()
        with pytest.raises(core.FileTooLargeError):
            await client.download(
                DriveFile("1A2b3C4d5E6f7G8h9I0jK", "big.iso", 50 * 1024**3, ""),
                tmp_path / "big.iso",
                max_bytes=1024**3,
            )
        await client.close()

    _run(go())


async def _ret(value, *extra):
    return (value, *extra)


# --------------------------------------------------------------------------- #
# Regressions found by the Oct 2026 research pass
# --------------------------------------------------------------------------- #
def test_public_download_uses_usercontent_host():
    """drive.google.com/uc drops confirm= on redirect -> virus-scan HTML page."""
    client = core.DriveClient()
    url, headers = asyncio.run(
        client._media_request(core.DriveFile("1AbCdEfGhIjKlMnOp", "v.mp4", 500, "video/mp4"))
    )
    assert "drive.usercontent.google.com/download" in url
    assert "confirm=t" in url
    assert "export=download" in url
    assert headers == {}


def test_confirm_handshake_rewrites_legacy_host():
    out = core._with_confirm(
        "https://drive.google.com/uc?export=download&id=1AbCdEfGhIjKlMnOp", "t"
    )
    assert out.startswith("https://drive.usercontent.google.com/download")
    assert "confirm=t" in out and "id=1AbCdEfGhIjKlMnOp" in out

    direct = core._with_confirm(
        "https://drive.usercontent.google.com/download?id=X&export=download", "ABC"
    )
    assert direct.endswith("confirm=ABC")


def test_authenticated_download_still_uses_drive_api():
    class Creds:
        is_service_account = True

        async def token(self):
            return "tok"

    client = core.DriveClient(credentials=Creds())  # type: ignore[arg-type]
    url, headers = asyncio.run(
        client._media_request(core.DriveFile("abc", "v.mp4", 1, "video/mp4"))
    )
    assert "googleapis.com/drive/v3/files/abc" in url and "alt=media" in url
    assert headers["Authorization"] == "Bearer tok"


# --------------------------------------------------------------------------- #
# Quota workaround (copy-to-my-Drive)
# --------------------------------------------------------------------------- #
def test_quota_error_detected_from_html():
    client = core.DriveClient()
    html = (
        "<html>Sorry, you can't view or download this file at this time. "
        "Too many users have viewed or downloaded this file recently.</html>"
    )
    msg = client._explain_html(html, core.DriveFile("x", "v.mp4", 1, "video/mp4"))
    assert "quota" in msg.lower()
    assert "/quotafix" in msg
    assert "VPN" in msg  # tells the user the useless workarounds don't help


def test_quota_help_mentions_real_fixes():
    assert "copy" in core.QUOTA_HELP.lower()
    assert "wait 1" in core.QUOTA_HELP.lower()
    assert isinstance(core.QuotaExceededError(core.QUOTA_HELP), core.DriveError)


def test_copy_requires_write_scope():
    class ReadOnlyCreds:
        is_service_account = False
        can_write = False

    client = core.DriveClient(credentials=ReadOnlyCreds())  # type: ignore[arg-type]
    with pytest.raises(core.DriveError) as err:
        asyncio.run(client.copy_to_my_drive("abc"))
    assert "full Drive access" in str(err.value)
    assert "DRIVE_SCOPE=full" in str(err.value)


def test_download_via_copy_cleans_up(monkeypatch, tmp_path):
    """copy -> download -> delete, even when the download succeeds."""
    client = core.DriveClient()

    class Creds:
        is_service_account = False
        can_write = True

    client.credentials = Creds()  # type: ignore[assignment]

    async def fake_get_file(file_id):
        return core.DriveFile(file_id, "v.mp4", 100, "video/mp4")

    async def fake_copy(file_id, name=None):
        return core.DriveFile("COPYID123", "v.mp4", 100, "video/mp4")

    deleted = []

    async def fake_delete(file_id):
        deleted.append(file_id)

    async def fake_download(info, dest, **kw):
        Path(dest).write_bytes(b"x" * 100)
        return Path(dest)

    client.get_file = fake_get_file           # type: ignore[assignment]
    client.copy_to_my_drive = fake_copy       # type: ignore[assignment]
    client.delete_file = fake_delete          # type: ignore[assignment]
    client.download = fake_download           # type: ignore[assignment]

    dest = tmp_path / "v.mp4"
    path, copy = asyncio.run(client.download_via_copy("ORIGINAL123", dest))
    assert path.exists()
    assert copy.id == "COPYID123"
    assert deleted == ["COPYID123"]           # temp copy removed from user's Drive


# --------------------------------------------------------------------------- #
# whoami — must NOT use oauth2/v3/userinfo (401 without openid/email scope)
# --------------------------------------------------------------------------- #
class _FakeResp:
    """Mimics an aiohttp response: `read()` returns the raw body bytes."""

    def __init__(self, status: int, payload=None, text: str = "", gzipped: bool = False):
        import json as _json

        self.status = status
        body = text if text else _json.dumps(payload or {})
        self._body = body.encode()
        if gzipped:
            import gzip as _gzip

            self._body = _gzip.compress(self._body)

    async def read(self):
        return self._body

    async def json(self):
        import json as _json

        return _json.loads(self._body.decode())

    async def text(self, errors="ignore"):
        return self._body.decode(errors=errors)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, resp: _FakeResp, recorder: list):
        self._resp = resp
        self._recorder = recorder

    def get(self, url, **kw):
        self._recorder.append(url)
        return self._resp


def _client_with(resp, recorder):
    from gdrive_core import DriveClient

    client = DriveClient()
    client.credentials = SimpleNamespace(
        is_service_account=False,
        email="fallback@example.com",
    )

    async def fake_token():
        return "ya29.fake"

    async def fake_session():
        return _FakeSession(resp, recorder)

    client.credentials.token = fake_token
    client.session = fake_session
    client._auth_headers = lambda: _async({"Authorization": "Bearer ya29.fake"})
    return client


def _async(value):
    async def _inner():
        return value

    return _inner()


def test_whoami_reads_identity_from_drive_about_not_userinfo():
    """A drive.readonly token has no openid scope -> userinfo answers 401."""
    seen: list[str] = []
    resp = _FakeResp(
        200,
        {
            "user": {"emailAddress": "me@example.com", "displayName": "Me", "me": True},
            "storageQuota": {"limit": "1000", "usage": "10"},
        },
    )
    client = _client_with(resp, seen)

    who = asyncio.run(client.whoami())

    assert who == "me@example.com"
    assert all("userinfo" not in url for url in seen), "must not call userinfo"
    assert any("/drive/v3/about" in url for url in seen)


def test_whoami_401_is_explained_not_raw():
    seen: list[str] = []
    resp = _FakeResp(401, text='{"error":{"message":"Invalid Credentials"}}')
    client = _client_with(resp, seen)

    with pytest.raises(DriveError) as err:
        asyncio.run(client.whoami())
    message = str(err.value)
    assert "401" in message
    assert "GOOGLE_REFRESH_TOKEN" in message      # tells the user what to fix
    assert "userinfo" not in message


def test_whoami_403_mentions_scope_or_api_disabled():
    seen: list[str] = []
    resp = _FakeResp(403, text="insufficientPermissions")
    client = _client_with(resp, seen)

    with pytest.raises(DriveError) as err:
        asyncio.run(client.whoami())
    assert "403" in str(err.value)


# --------------------------------------------------------------------------- #
# gzip metadata responses (the "UnicodeDecodeError: 0x8b" bug)
# --------------------------------------------------------------------------- #
def test_read_json_inflates_gzip_body():
    """Google gzips metadata; auto_decompress is off for the byte pump."""
    payload = {"files": [{"id": "abc", "name": "Lecture 1.mp4"}], "nextPageToken": None}
    resp = _FakeResp(200, payload, gzipped=True)

    assert asyncio.run(core.read_json(resp)) == payload


def test_read_json_handles_plain_body():
    resp = _FakeResp(200, {"user": {"emailAddress": "a@b.c"}})
    assert asyncio.run(core.read_json(resp))["user"]["emailAddress"] == "a@b.c"


def test_read_json_never_raises_unicodeerror():
    """A corrupt body must never surface as a codec crash."""
    class BadResp:
        status = 200

        async def read(self):
            return b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff" + b"\x00not really gzip"

    result = asyncio.run(core.read_json(BadResp()))   # must not raise UnicodeDecodeError
    assert isinstance(result, dict)


def test_read_text_inflates_gzip_error_page():
    body = "<html>quota exceeded</html>".encode()
    import gzip as _gzip

    class Resp:
        async def read(self):
            return _gzip.compress(body)

    assert "quota exceeded" in asyncio.run(core.read_text(Resp()))


def test_folder_listing_survives_gzip(monkeypatch):
    """The real symptom: 'Folder scan failed: UnicodeDecodeError'."""
    payload = {
        "files": [
            {"id": "f1", "name": "Lecture 01.mp4", "mimeType": "video/mp4", "size": "1048576"}
        ]
    }
    seen: list[str] = []

    class Session:
        def get(self, url, **kw):
            seen.append(url)
            return _FakeResp(200, payload, gzipped=True)

    client = DriveClient()
    client.credentials = SimpleNamespace(is_service_account=False, email="x@y.z")
    client.api_key = "fake-key"          # makes iter_folder consider itself authorized

    async def fake_session():
        return Session()

    async def fake_auth():
        return {"Authorization": "Bearer t"}

    client.session = fake_session
    client._auth_headers = fake_auth

    async def collect():
        return [item async for item in client.iter_folder("root", recursive=False)]

    items = asyncio.run(collect())
    assert [i.name for i in items] == ["Lecture 01.mp4"]


def test_session_asks_for_identity_encoding():
    """Regression: aiohttp's default "gzip, deflate, br, zstd" + auto_decompress
    off = gzip bodies reaching json()/text() -> UnicodeDecodeError 0x8b."""
    client = DriveClient()

    async def build():
        session = await client.session()
        enc = session.headers.get("Accept-Encoding")
        await client.close()
        return enc

    assert asyncio.run(build()) == "identity"
