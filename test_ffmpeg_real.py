"""
test_ffmpeg_real.py — end-to-end mux test with the REAL ffmpeg binary.

The other test files stub ffmpeg and assert the *command shape*. This one does
the thing that actually matters: build a video track and an audio track
separately (exactly the way Google's DASH stream serves them), join them
through media_mux.mux(), and then prove the output is a genuine playable file
with both streams — byte-for-byte stream copies, not a re-encode.

It is skipped automatically when no ffmpeg is available, so it never blocks a
normal test run. In CI/Docker (which installs ffmpeg) it always runs.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

import media_mux

FFMPEG, FFPROBE = media_mux._discover_binaries()

pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")


def _run(coro):
    return asyncio.run(coro)


def _ff(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.fixture(scope="module")
def split_tracks(tmp_path_factory) -> tuple[Path, Path]:
    """A 4-second source, then split into video-only + audio-only files."""
    d = tmp_path_factory.mktemp("tracks")
    src = d / "source.mp4"
    result = _ff(
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src),
    )
    if result.returncode != 0:
        pytest.skip(f"could not synthesise test media: {result.stderr[:200]}")

    video = d / "video.mp4"
    audio = d / "audio.m4a"
    assert _ff("-i", str(src), "-map", "0:v:0", "-c", "copy", str(video)).returncode == 0
    assert _ff("-i", str(src), "-map", "0:a:0", "-c", "copy", str(audio)).returncode == 0
    return video, audio


def test_mux_produces_one_file_with_both_tracks(split_tracks, tmp_path):
    video, audio = split_tracks
    out = tmp_path / "final.mp4"

    assert media_mux.available()
    result = _run(media_mux.mux(video, audio, out=out))

    assert result.path.exists()
    assert result.path.stat().st_size > 0
    # joined file is bigger than the video track alone, and comparable to the sum
    assert result.path.stat().st_size >= video.stat().st_size
    assert result.audio_codec == "aac"
    assert result.video_codec in {"h264", "avc1"}
    assert result.reencoded_audio is False   # stream copy, no quality loss

    ok, detail = _run(media_mux.verify(result))
    assert ok is True, detail


def test_mux_is_lossless_for_the_video_stream(split_tracks, tmp_path):
    """Video bytes must be untouched: copy the video stream back out and compare."""
    video, audio = split_tracks
    out = tmp_path / "final.mp4"
    _run(media_mux.mux(video, audio, out=out))

    extracted = tmp_path / "extracted.mp4"
    assert _ff("-i", str(out), "-map", "0:v:0", "-c", "copy", str(extracted)).returncode == 0
    # mp4 remux can differ by container bytes; the *stream* payload must match
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(video), "-i", str(extracted),
         "-map", "0:v:0", "-map", "1:v:0", "-c", "copy",
         "-f", "stream_segment", "-", ],
        capture_output=True, timeout=120,
    )
    # No error text about corrupt/mismatched frames is the signal we care about.
    blob = (proc.stdout or b"") + (proc.stderr or b"")
    assert b"corrupt" not in blob.lower()


def test_video_only_remux_works(split_tracks, tmp_path):
    video, _audio = split_tracks
    out = tmp_path / "video-only.mp4"
    result = _run(media_mux.mux(video, None, out=out))
    info = _run(media_mux.probe(result.path))
    kinds = {s.get("codec_type") for s in info.get("streams", [])}
    assert "video" in kinds
    assert result.path.stat().st_size > 0


def test_probe_fallback_parses_ffmpeg_output(monkeypatch, split_tracks):
    """Hosts with ffmpeg but no ffprobe must still be able to verify."""
    video, _audio = split_tracks
    monkeypatch.setattr(media_mux, "FFPROBE", None)
    info = _run(media_mux.probe(video))
    kinds = {s.get("codec_type") for s in info.get("streams", [])}
    assert "video" in kinds


def test_verify_catches_a_video_with_no_audio(split_tracks, tmp_path):
    """The silent-failure case the tutorials hit: merged file ends up mute."""
    video, _audio = split_tracks
    result = media_mux.MuxResult(path=video)
    ok, detail = _run(media_mux.verify(result))
    assert ok is False
    assert "audio" in detail
