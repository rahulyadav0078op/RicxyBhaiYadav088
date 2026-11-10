"""
test_media_download.py — the stream-track downloader, tested against a real
local HTTP server (aiohttp) instead of a mock.

What is actually asserted:
  * the tutorial's step 7 (`&range=` stripping) really happens
  * only the *whole* track is fetched, never the fragment the URL asked for
  * HTTP Range resume works: a half-finished `.part` continues, not restarts
  * a stale/expired link (403) raises a clear, user-facing error
  * an HTML "sign in" page is detected instead of being saved as video.mp4
  * cancellation stops the download

No network access is required — the server is local.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiohttp
from aiohttp import web
import pytest

import media_download as md

PAYLOAD = bytes(range(256)) * 4096  # 1 MiB of deterministic bytes


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def server():
    """A tiny 'videoplayback' server implementing Range requests."""

    async def handler(request: web.Request) -> web.StreamResponse:
        mode = request.query.get("mode", "ok")
        if mode == "forbidden":
            return web.Response(status=403, text="expired")
        if mode == "html":
            return web.Response(
                text="<html><body>Sign in - Google Accounts</body></html>",
                content_type="text/html",
            )
        start = 0
        rng = request.headers.get("Range")
        partial = False
        if rng and rng.startswith("bytes="):
            start = int(rng.split("=")[1].split("-")[0])
            partial = True
        body = PAYLOAD[start:]
        resp = web.StreamResponse(
            status=206 if partial else 200,
            headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(len(body)),
                "Accept-Ranges": "bytes",
            },
        )
        if partial:
            resp.headers["Content-Range"] = f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}"
        await resp.prepare(request)
        for i in range(0, len(body), 65536):
            await resp.write(body[i : i + 65536])
        await resp.write_eof()
        return resp

    async def with_server(coro_fn):
        app = web.Application()
        app.router.add_get("/videoplayback", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        try:
            async with aiohttp.ClientSession() as session:
                return await coro_fn(f"http://127.0.0.1:{port}/videoplayback", session)
        finally:
            await runner.cleanup()

    return with_server


# --------------------------------------------------------------------------- #
# pure functions
# --------------------------------------------------------------------------- #
def test_range_tail_is_stripped():
    url = "https://r1.example.com/videoplayback?itag=22&mime=video%2Fmp4&range=0-262143"
    assert md.clean_stream_url(url) == (
        "https://r1.example.com/videoplayback?itag=22&mime=video%2Fmp4"
    )


def test_html_escaped_and_escaped_slashes_are_repaired():
    url = "https://x/videoplayback?id=1\\u0026mime=audio\\/mp4\\u0026range=5-9"
    cleaned = md.clean_stream_url(url)
    assert "\\u0026" not in cleaned and "\\/" not in cleaned
    assert cleaned.endswith("mime=audio/mp4")


def test_non_stream_url_is_rejected_early():
    with pytest.raises(md.StreamFetchError) as err:
        _run(md.fetch_track(None, "https://drive.google.com/file/d/abc/view", Path("/tmp/x.mp4")))
    assert "not a Drive stream URL" in str(err.value)


def test_extension_guess():
    assert md.guess_extension("video/webm", "video") == ".webm"
    assert md.guess_extension("audio/mp4", "audio") == ".m4a"
    assert md.guess_extension("video/mp4", "video") == ".mp4"


# --------------------------------------------------------------------------- #
# real downloads against the local server
# --------------------------------------------------------------------------- #
def test_full_track_downloads_despite_range_in_url(server, tmp_path):
    async def scenario(base, session):
        url = f"{base}?mime=video/mp4&range=0-1023"  # tutorial's broken URL
        return await md.fetch_track(session, url, tmp_path / "video.mp4")

    track = _run(server(scenario))
    assert track.bytes_written == len(PAYLOAD)
    assert track.path.read_bytes() == PAYLOAD
    assert not (tmp_path / "video.mp4.part").exists()


def test_resume_continues_from_partial_file(server, tmp_path):
    dest = tmp_path / "audio.m4a"
    half = len(PAYLOAD) // 2
    (tmp_path / "audio.m4a.part").write_bytes(PAYLOAD[:half])

    async def scenario(base, session):
        return await md.fetch_track(session, f"{base}?mime=audio/mp4", dest, kind="audio")

    track = _run(server(scenario))
    assert track.resumed is True
    assert track.path.read_bytes() == PAYLOAD  # nothing lost, nothing doubled


def test_progress_callback_reports_growth(server, tmp_path):
    seen: list[tuple[int, int | None]] = []

    async def on_progress(done, total):
        seen.append((done, total))

    async def scenario(base, session):
        return await md.fetch_track(
            session, f"{base}?mime=video/mp4", tmp_path / "v.mp4", on_progress=on_progress
        )

    _run(server(scenario))
    assert seen, "no progress was reported"
    assert seen[-1][0] == len(PAYLOAD)
    assert all(isinstance(d, int) for d, _ in seen)


def test_expired_link_gives_actionable_error(server, tmp_path):
    async def scenario(base, session):
        return await md.fetch_track(session, f"{base}?mode=forbidden", tmp_path / "v.mp4")

    with pytest.raises(md.StreamFetchError) as err:
        _run(server(scenario))
    assert "expire" in str(err.value) or "refused" in str(err.value)


def test_signin_html_is_not_saved_as_video(server, tmp_path):
    async def scenario(base, session):
        return await md.fetch_track(session, f"{base}?mode=html", tmp_path / "v.mp4")

    with pytest.raises(md.StreamFetchError) as err:
        _run(server(scenario))
    assert "private" in str(err.value) or "sign-in" in str(err.value)
    assert not (tmp_path / "v.mp4").exists()


def test_cancel_stops_before_anything_is_written(server, tmp_path):
    cancel = asyncio.Event()
    cancel.set()

    async def scenario(base, session):
        return await md.fetch_track(
            session, f"{base}?mime=video/mp4", tmp_path / "v.mp4", cancel=cancel
        )

    with pytest.raises(md.StreamFetchError) as err:
        _run(server(scenario))
    assert "Cancelled" in str(err.value)
