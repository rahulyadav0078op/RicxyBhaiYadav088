"""
test_media_mux.py — mux logic tested with a fake ffmpeg (no binary needed).

The real ffmpeg is stubbed so we can assert the *command shape*: stream copy,
correct maps, -shortest, faststart, and the fallback paths. That is where the
bugs actually live; the encoding itself is ffmpeg's job.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import media_mux as muxmod


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def fake_ffmpeg(monkeypatch, tmp_path):
    """Stub ffmpeg/ffprobe: records argv, writes a plausible output file."""
    calls: list[list[str]] = []
    probe_payload = {"streams": [], "format": {"duration": "42.5"}}

    async def fake_run(cmd, timeout=3600):
        calls.append(cmd)
        out_file = Path(cmd[-1])
        if out_file.suffix in (".mp4", ".mkv"):
            out_file.write_bytes(b"x" * 2048)
        return 0, "", ""

    async def fake_probe(path):
        return probe_payload

    monkeypatch.setattr(muxmod, "FFMPEG", "/usr/bin/ffmpeg")
    monkeypatch.setattr(muxmod, "_run", fake_run)
    monkeypatch.setattr(muxmod, "probe", fake_probe)
    return calls, tmp_path


def _touch(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"y" * 1024)
    return p


def test_audio_present_raises_clear_error(monkeypatch):
    monkeypatch.setattr(muxmod, "FFMPEG", None)
    with pytest.raises(muxmod.MuxError) as err:
        _run(muxmod.mux(Path("v.mp4"), Path("a.m4a")))
    assert "ffmpeg is not installed" in str(err.value)


def test_missing_track_is_reported(fake_ffmpeg):
    _calls, tmp_path = fake_ffmpeg
    with pytest.raises(muxmod.MuxError) as err:
        _run(muxmod.mux(tmp_path / "nope.mp4", tmp_path / "nope.m4a"))
    assert "missing or empty" in str(err.value)


def test_stream_copy_command_shape(fake_ffmpeg):
    """The whole point: -c copy, both maps, -shortest, faststart."""
    calls, tmp_path = fake_ffmpeg
    video = _touch(tmp_path, "video.mp4")
    audio = _touch(tmp_path, "audio.m4a")

    result = _run(muxmod.mux(video, audio, out=tmp_path / "final.mp4"))

    cmd = calls[0]
    assert cmd[0] == "/usr/bin/ffmpeg"
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
    assert cmd[cmd.index("-map") + 1] == "0:v:0"
    assert cmd[cmd.index("-map", cmd.index("-map") + 1) + 1] == "1:a:0"
    assert "-shortest" in cmd
    assert "-movflags" in cmd and "+faststart" in cmd
    # never a filter / re-encode flag for the video
    assert "-c:v" not in cmd and "-vf" not in cmd
    assert result.path.name == "final.mp4"
    assert result.duration == 42.5


def test_offset_is_applied_to_audio_input(fake_ffmpeg):
    calls, tmp_path = fake_ffmpeg
    video = _touch(tmp_path, "v.mp4")
    audio = _touch(tmp_path, "a.m4a")

    _run(muxmod.mux(video, audio, out=tmp_path / "o.mp4", offset=-0.35))

    cmd = calls[0]
    assert "-itsoffset" in cmd
    assert cmd[cmd.index("-itsoffset") + 1] == "-0.350"


def test_video_only_remux_has_no_audio_map(fake_ffmpeg):
    calls, tmp_path = fake_ffmpeg
    video = _touch(tmp_path, "v.mp4")

    _run(muxmod.mux(video, None, out=tmp_path / "o.mp4"))

    cmd = calls[0]
    assert "1:a:0" not in cmd
    assert "-shortest" not in cmd
    assert cmd[cmd.index("-c") + 1] == "copy"


def test_unsafe_audio_codec_reencodes_audio_only(fake_ffmpeg, monkeypatch):
    """A codec MP4 dislikes must not silently lose audio."""
    calls, tmp_path = fake_ffmpeg

    async def probe_with_tracks(path):
        return {
            "streams": [
                {"codec_type": "video", "codec_name": "vp9"},
                {"codec_type": "audio", "codec_name": "vorbis"},
            ],
            "format": {"duration": "10"},
        }

    monkeypatch.setattr(muxmod, "probe", probe_with_tracks)
    video = _touch(tmp_path, "v.mp4")
    audio = _touch(tmp_path, "a.m4a")

    result = _run(muxmod.mux(video, audio, out=tmp_path / "o.mp4"))

    cmd = calls[0]
    assert "-c:a" in cmd and "aac" in cmd
    assert cmd[cmd.index("-c") + 1] == "copy"   # video still copied
    assert result.reencoded_audio is True


def test_unsafe_audio_codec_falls_back_to_mkv_when_reencode_disabled(fake_ffmpeg, monkeypatch):
    calls, tmp_path = fake_ffmpeg

    async def probe_with_tracks(path):
        return {
            "streams": [
                {"codec_type": "video", "codec_name": "vp9"},
                {"codec_type": "audio", "codec_name": "vorbis"},
            ],
            "format": {"duration": "10"},
        }

    monkeypatch.setattr(muxmod, "probe", probe_with_tracks)
    video = _touch(tmp_path, "v.mp4")
    audio = _touch(tmp_path, "a.m4a")

    result = _run(
        muxmod.mux(video, audio, out=tmp_path / "o.mp4", reencode_audio_if_needed=False)
    )
    assert result.path.suffix == ".mkv"
    assert "-c:a" not in calls[0]


def test_ffmpeg_failure_surfaces_stderr(fake_ffmpeg, monkeypatch):
    _calls, tmp_path = fake_ffmpeg

    async def failing_run(cmd, timeout=3600):
        return 1, "", "Error: could not find codec parameters"

    monkeypatch.setattr(muxmod, "_run", failing_run)
    video = _touch(tmp_path, "v.mp4")
    audio = _touch(tmp_path, "a.m4a")

    with pytest.raises(muxmod.MuxError) as err:
        _run(muxmod.mux(video, audio, out=tmp_path / "o.mp4"))
    assert "could not find codec" in str(err.value)


def test_verify_detects_silent_audio_loss(monkeypatch, tmp_path):
    async def probe_video_only(path):
        return {"streams": [{"codec_type": "video", "codec_name": "h264"}], "format": {}}

    monkeypatch.setattr(muxmod, "probe", probe_video_only)
    ok, message = _run(muxmod.verify(muxmod.MuxResult(path=tmp_path / "x.mp4")))
    assert ok is False and "no audio" in message


def test_verify_passes_with_both_tracks(monkeypatch, tmp_path):
    async def probe_both(path):
        return {
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"duration": "60"},
        }

    monkeypatch.setattr(muxmod, "probe", probe_both)
    result = muxmod.MuxResult(path=tmp_path / "x.mp4", duration=60, audio_codec="aac")
    ok, message = _run(muxmod.verify(result))
    assert ok is True and "h264" in message
