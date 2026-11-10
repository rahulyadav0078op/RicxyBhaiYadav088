"""
media_download.py — download ONE media track (video-only or audio-only) to disk.

WHY THIS EXISTS
---------------
Google Drive's preview player streams a video as **two separate HTTP resources**:
a video-only track and an audio-only track (`...mime=video/mp4` and
`...mime=audio/mp4`, served from a `videoplayback` URL). That is why every
"download view-only Drive video" tutorial ends with "the file has no audio — 
download the audio separately and merge them".

Step 7 of those tutorials is the fiddly one: *delete everything from `&range=`
onwards in the URL*. If you leave `&range=0-99999` in, the browser/curl happily
downloads exactly that fragment and you end up with a broken 100 KB "video".
`clean_stream_url()` performs that edit in code so it can never be forgotten.

`fetch_track()` is the download half:
  * resumable (.part + HTTP Range) so a dropped connection does not restart a
    900 MB track from zero
  * retries with exponential backoff
  * detects Google's "you must sign in" / "virus scan" HTML pages and says so
    plainly instead of saving a 4 KB HTML file as "video.mp4"
  * reports progress through the same callback the rest of the bot uses

The mux half lives in media_mux.py.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

# Progress callback: (bytes_done, bytes_total_or_None) -> awaitable
ProgressCb = Callable[[int, Optional[int]], Awaitable[None]]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

CHUNK = 1 << 20  # 1 MiB

# How many bytes of the response to sniff when deciding "is this really media?"
_SNIFF = 512


class StreamFetchError(Exception):
    """Human-readable failure while fetching a stream track."""


@dataclass
class FetchedTrack:
    path: Path
    kind: str          # "video" | "audio"
    bytes_written: int
    resumed: bool = False

    @property
    def size_mb(self) -> float:
        return self.bytes_written / (1024 * 1024)


# --------------------------------------------------------------------------- #
# URL hygiene — the tutorial's step 7, in code
# --------------------------------------------------------------------------- #
def clean_stream_url(url: str) -> str:
    """Strip the `&range=…` fragment so the whole track downloads.

    A `videoplayback` URL copied out of Chrome DevTools ends with something like
    `...&range=0-262143`. That parameter makes Google return one byte-range
    only. Removing it (and the siblings `&rn=`, `&rbuf=`) makes the same URL
    serve the entire file. This is exactly what the video tells you to do by
    hand.
    """
    if not url:
        return ""
    url = url.strip().strip('"').strip("'")
    # HTML-escaped ampersands come back from some scraped pages.
    url = url.replace("&amp;", "&").replace("\\u0026", "&").replace("\\/", "/")
    for marker in ("&range=", "?range="):
        idx = url.find(marker)
        if idx != -1:
            url = url[:idx]
            break
    # Drop dangling separators left behind.
    return url.rstrip("&?")


def looks_like_stream_url(url: str) -> bool:
    return bool(url) and "videoplayback" in url


def guess_extension(content_type: str, kind: str) -> str:
    """Pick a container for the track from the response's content type."""
    ct = (content_type or "").lower()
    if "webm" in ct:
        return ".webm"
    if "audio" in ct or kind == "audio":
        return ".m4a"
    return ".mp4"


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
async def fetch_track(
    session,
    url: str,
    dest: Path,
    *,
    kind: str = "video",
    on_progress: Optional[ProgressCb] = None,
    cancel: Optional[asyncio.Event] = None,
    retries: int = 4,
    timeout: float = 300.0,
) -> FetchedTrack:
    """Download one track to `dest`, resuming across retries.

    `session` is an aiohttp.ClientSession (the bot reuses its Drive session).
    Raises StreamFetchError with a message that is safe to show the user.
    """
    url = clean_stream_url(url)
    if not looks_like_stream_url(url):
        raise StreamFetchError(
            "This is not a Drive stream URL. Expected a `videoplayback` link; "
            "re-open the Drive preview and copy it again."
        )

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    attempt = 0
    resumed = part.exists() and part.stat().st_size > 0
    last_error = "unknown error"

    while attempt <= retries:
        if cancel is not None and cancel.is_set():
            raise StreamFetchError("Cancelled.")

        attempt += 1
        done = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        if done:
            headers["Range"] = f"bytes={done}-"

        try:
            async with session.get(url, headers=headers, timeout=timeout) as r:
                if r.status == 416:
                    break  # already complete
                if r.status in (401, 403):
                    raise StreamFetchError(
                        "Google refused this stream link (HTTP "
                        f"{r.status}). Stream links expire after a few minutes "
                        "and only work for files shared publicly or with this "
                        "account — re-run the command to get a fresh link."
                    )
                if r.status >= 400:
                    last_error = f"HTTP {r.status}"
                    raise StreamFetchError(f"Stream download failed: HTTP {r.status}")

                ctype = r.headers.get("Content-Type", "")
                if "text/html" in ctype.lower():
                    snippet = (await r.content.read(_SNIFF)).decode("utf-8", "ignore").lower()
                    if "sign in" in snippet or "accounts.google.com" in snippet:
                        raise StreamFetchError(
                            "Google served the sign-in page instead of media. The "
                            "file is private — use /auth credentials (OAuth) and "
                            "let the normal download path handle it."
                        )
                    if "virus" in snippet or "too large" in snippet:
                        raise StreamFetchError(
                            "Google served the 'can't scan for viruses' page "
                            "instead of media. Re-run, or use /quotafix."
                        )
                    raise StreamFetchError(
                        "Google returned an HTML page instead of media. The "
                        "stream link is stale — re-run the command."
                    )

                total = None
                if r.status == 206:
                    crange = r.headers.get("Content-Range", "")
                    if "/" in crange:
                        tail = crange.split("/")[-1].strip()
                        if tail.isdigit():
                            total = int(tail)
                elif r.headers.get("Content-Length"):
                    clen = r.headers["Content-Length"]
                    if clen.isdigit():
                        total = int(clen)

                mode = "ab" if done else "wb"
                wrote = 0
                with part.open(mode) as fh:
                    async for chunk in r.content.iter_chunked(CHUNK):
                        if cancel is not None and cancel.is_set():
                            raise StreamFetchError("Cancelled.")
                        if not chunk:
                            continue
                        fh.write(chunk)
                        wrote += len(chunk)
                        if on_progress is not None:
                            await on_progress(done + wrote, total)

            final_size = part.stat().st_size if part.exists() else 0
            if total is not None and final_size < total:
                last_error = f"incomplete ({final_size}/{total} bytes)"
                continue  # retry, resuming from what we have
            break

        except StreamFetchError:
            raise
        except (asyncio.TimeoutError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt > retries:
                break
            await asyncio.sleep(min(2 ** attempt, 15))

    else:  # loop exhausted
        pass

    if not part.exists() or part.stat().st_size == 0:
        raise StreamFetchError(f"Track download failed ({last_error}).")

    size = part.stat().st_size
    # Very small "success" is almost always an error page we failed to sniff.
    if size < 20_000 and dest.suffix in (".mp4", ".m4a", ".webm"):
        sample = part.read_bytes()[:64]
        if b"<" in sample or b"{" in sample:
            part.unlink(missing_ok=True)
            raise StreamFetchError(
                "The download is only a few KB and looks like a web page, not "
                "media. The stream link is probably stale — re-run the command."
            )

    part.replace(dest)
    return FetchedTrack(path=dest, kind=kind, bytes_written=size, resumed=resumed)


async def fetch_both(
    session,
    video_url: str,
    audio_url: str,
    workdir: Path,
    *,
    on_progress: Optional[ProgressCb] = None,
    cancel: Optional[asyncio.Event] = None,
) -> tuple[FetchedTrack, Optional[FetchedTrack]]:
    """Download the video track, then the audio track, into `workdir`.

    Sequential on purpose: two parallel streams from the same Google host trip
    per-connection throttling and gain nothing on a 1 vCPU host.
    """
    workdir = Path(workdir)
    video = await fetch_track(
        session,
        video_url,
        workdir / "video.mp4",
        kind="video",
        on_progress=on_progress,
        cancel=cancel,
    )
    audio = None
    if audio_url:
        audio = await fetch_track(
            session,
            audio_url,
            workdir / "audio.m4a",
            kind="audio",
            on_progress=on_progress,
            cancel=cancel,
        )
    return video, audio
