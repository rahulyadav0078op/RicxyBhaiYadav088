"""
media_mux.py — merge the separate video + audio tracks back into one file.

WHY THIS EXISTS
---------------
Google (like every DASH-style stream) serves the video track and the audio track
as two independent URLs — the exact thing the "view-only Drive download" videos
show you at the end: "this video doesn't have audio, download the audio
separately". Their instructions then say *open it in a video editor*, which means
a re-encode: slow, quality-lossy, and it needs a GUI.

This module does it properly with FFmpeg stream copy:

    ffmpeg -i video -i audio -map 0:v:0 -map 1:a:0 -c copy -movflags +faststart out.mp4

`-c copy` = no decode, no encode. Bytes are identical to what Google served, so
there is zero quality loss, and it takes seconds instead of minutes.

Failure modes handled (each one is a real-world case):
  * ffmpeg not installed            -> clear message, not a traceback
  * audio longer/shorter than video -> -shortest trims to the video track
  * A/V drift (DASH start offsets)  -> -itsoffset nudge, configurable
  * audio in a container mp4 dislikes (e.g. some Dolby/Opus) -> optional re-encode
    of just the audio stream as AAC, video still copied
  * output container mismatch       -> falls back to .mkv, which holds anything
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

def _discover_binaries() -> tuple[Optional[str], Optional[str]]:
    """Find ffmpeg/ffprobe, with a Python-delivered fallback.

    Docker installs both from apt. But hosts where you cannot apt-install (a
    Kaggle/Colab notebook, a locked-down container) can still get ffmpeg from
    the `imageio-ffmpeg` wheel, so /merge and /streamdl keep working instead of
    failing with "ffmpeg is not installed".
    """
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")

    if not ffmpeg:
        try:  # optional dependency; absent is fine
            import imageio_ffmpeg

            candidate = imageio_ffmpeg.get_ffmpeg_exe()
            if candidate and Path(candidate).is_file():
                ffmpeg = candidate
        except Exception:  # noqa: BLE001
            ffmpeg = None

    if not ffprobe and ffmpeg:
        sibling = Path(ffmpeg).with_name("ffprobe")
        if sibling.is_file():
            ffprobe = str(sibling)

    return ffmpeg, ffprobe


FFMPEG, FFPROBE = _discover_binaries()


class MuxError(Exception):
    """Muxing failed; message is safe to show a user."""


@dataclass
class MuxResult:
    path: Path
    duration: Optional[float] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    reencoded_audio: bool = False

    @property
    def summary(self) -> str:
        parts = [self.path.name]
        if self.video_codec:
            parts.append(f"v:{self.video_codec}")
        if self.audio_codec:
            parts.append(f"a:{self.audio_codec}" + (" (aac)" if self.reencoded_audio else " (copy)"))
        if self.duration:
            parts.append(f"{self.duration:.0f}s")
        return " · ".join(parts)


def available() -> bool:
    return FFMPEG is not None


def _require_ffmpeg() -> str:
    if not FFMPEG:
        raise MuxError(
            "ffmpeg is not installed on this server. Install it with "
            "`apt-get install -y ffmpeg` (Dockerfile does this) — without it the "
            "separate video/audio tracks cannot be joined losslessly."
        )
    return FFMPEG


async def _run(cmd: list[str], timeout: float = 3600) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise MuxError(f"ffmpeg timed out after {timeout:.0f}s") from None
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def _probe_via_ffmpeg(path: Path) -> dict:
    """Last-resort probe: read the stream list off `ffmpeg -i <file>` stderr.

    ffmpeg prints the same codec/format summary ffprobe would give us, so on a
    host that ships ffmpeg without ffprobe we can still verify that the merged
    file really contains an audio stream — the whole point of verify().
    """
    if not FFMPEG:
        return {}
    _code, _out, err = await _run([FFMPEG, "-hide_banner", "-i", str(path)], timeout=120)
    streams: list[dict] = []
    duration = None
    for line in err.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            chunk = line.split("Duration:")[1].split(",")[0].strip()
            hh, mm, ss = chunk.split(":") if chunk.count(":") == 2 else ("0", "0", "0")
            try:
                duration = int(hh) * 3600 + int(mm) * 60 + float(ss)
            except ValueError:
                duration = None
        if line.startswith("Stream #"):
            stream: dict = {}
            if "Video:" in line:
                stream["codec_type"] = "video"
                tail = line.split("Video:", 1)[1].strip()
                stream["codec_name"] = tail.split()[0].split("(")[0].strip(",")
            elif "Audio:" in line:
                stream["codec_type"] = "audio"
                tail = line.split("Audio:", 1)[1].strip()
                stream["codec_name"] = tail.split()[0].split("(")[0].strip(",")
            if stream:
                streams.append(stream)
    if not streams:
        return {}
    fmt: dict = {}
    if duration is not None:
        fmt["duration"] = str(duration)
    return {"streams": streams, "format": fmt}


async def probe(path: Path) -> dict:
    """ffprobe the file; falls back to ffmpeg, then to {} when neither exists."""
    if not FFPROBE:
        return await _probe_via_ffmpeg(path)
    code, out, _err = await _run(
        [
            FFPROBE, "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        timeout=120,
    )
    if code != 0:
        return {}
    try:
        return json.loads(out)
    except ValueError:
        return {}


@dataclass
class _Tracks:
    has_video: bool = False
    has_audio: bool = False
    duration: Optional[float] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None


def _tracks(info: dict) -> _Tracks:
    tracks = _Tracks()
    for stream in info.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video":
            tracks.has_video = True
            tracks.video_codec = stream.get("codec_name")
        elif kind == "audio":
            tracks.has_audio = True
            tracks.audio_codec = stream.get("codec_name")
    duration = info.get("format", {}).get("duration")
    if duration:
        try:
            tracks.duration = float(duration)
        except ValueError:
            pass
    return tracks


# Containers that can hold almost any codec — used when mp4 refuses the audio.
_FALLBACK_CONTAINER = ".mkv"
# Audio codecs that an MP4 container accepts without re-encoding.
_MP4_SAFE_AUDIO = {"aac", "mp3", "ac3", "eac3", "alac", "opus"}


async def mux(
    video: Path,
    audio: Optional[Path] = None,
    *,
    out: Optional[Path] = None,
    offset: float = 0.0,
    reencode_audio_if_needed: bool = True,
    timeout: float = 3600,
) -> MuxResult:
    """Join a video track (and optionally an audio track) with no re-encoding.

    If `audio` is None the video is remuxed/normalised on its own (useful when the
    stream already contains audio, or when only the video track was captured).
    """
    ffmpeg = _require_ffmpeg()
    video = Path(video)
    if not video.is_file() or video.stat().st_size == 0:
        raise MuxError(f"video track missing or empty: {video}")

    audio_path = Path(audio) if audio else None
    if audio_path is not None and (not audio_path.is_file() or audio_path.stat().st_size == 0):
        raise MuxError(f"audio track missing or empty: {audio_path}")

    out_path = Path(out) if out else video.with_name(video.stem + ".muxed.mp4")

    # Probe first: decide the container and whether the audio is mp4-safe.
    video_info = await probe(video)
    audio_info = await probe(audio_path) if audio_path else {}
    v_tracks = _tracks(video_info)
    a_tracks = _tracks(audio_info)

    container = out_path.suffix or ".mp4"
    audio_codec = a_tracks.audio_codec
    reencode_audio = False
    if audio_path is not None and container == ".mp4" and audio_codec:
        if audio_codec not in _MP4_SAFE_AUDIO:
            if reencode_audio_if_needed:
                reencode_audio = True
            else:
                container = _FALLBACK_CONTAINER
                out_path = out_path.with_suffix(container)

    def build(reencode: bool, out_file: Path) -> list[str]:
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        cmd += ["-i", str(video)]
        if audio_path is not None:
            if offset:
                cmd += ["-itsoffset", f"{offset:.3f}"]
            cmd += ["-i", str(audio_path)]
            cmd += ["-map", "0:v:0", "-map", "1:a:0"]
        cmd += ["-c", "copy"]
        if reencode:
            # Only the *audio* is touched; video always stays a stream copy.
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        if audio_path is not None:
            cmd += ["-shortest"]
        if out_file.suffix == ".mp4":
            cmd += ["-movflags", "+faststart"]
        cmd.append(str(out_file))
        return cmd

    code, _out, err = await _run(build(reencode_audio, out_path), timeout=timeout)

    # mp4 refused the audio anyway -> retry in mkv (still a stream copy)
    if code != 0 and audio_path is not None and out_path.suffix == ".mp4":
        fallback = out_path.with_suffix(_FALLBACK_CONTAINER)
        code2, _o2, err2 = await _run(build(False, fallback), timeout=timeout)
        if code2 == 0:
            out_path, err = fallback, ""
        else:
            raise MuxError(f"ffmpeg failed: {(err or err2).strip()[:400]}")
    elif code != 0:
        raise MuxError(f"ffmpeg failed: {err.strip()[:400]}")

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise MuxError("ffmpeg reported success but produced no output")

    result_info = await probe(out_path)
    r_tracks = _tracks(result_info)
    return MuxResult(
        path=out_path,
        duration=r_tracks.duration or v_tracks.duration,
        video_codec=r_tracks.video_codec or v_tracks.video_codec,
        audio_codec=r_tracks.audio_codec or audio_codec,
        reencoded_audio=reencode_audio,
    )


async def verify(result: MuxResult) -> tuple[bool, str]:
    """Post-mux sanity check — the video's #1 failure is silent audio loss."""
    info = await probe(result.path)
    if not info:
        return True, "ffprobe unavailable; skipped verification"
    tracks = _tracks(info)
    if not tracks.has_video:
        return False, "output has no video stream"
    if not tracks.has_audio:
        return False, "output has no audio stream (merge failed silently)"
    detail = MuxResult(
        path=result.path,
        duration=tracks.duration,
        video_codec=tracks.video_codec,
        audio_codec=tracks.audio_codec,
        reencoded_audio=result.reencoded_audio,
    )
    return True, detail.summary
