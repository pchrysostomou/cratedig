"""Phase 3 tests for YouTubeDownloader — fully mocked (no network/yt-dlp/ffmpeg)."""

from __future__ import annotations

from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError as YtdlpDownloadError

from cratedig.download import youtube_downloader as mod
from cratedig.download.youtube_downloader import (
    YouTubeDownloader,
    _output_ext,
    _safe_filename,
)
from cratedig.exceptions import DownloadError
from cratedig.models import Track


def _track(title="Song", artists=("Artist A",)):
    return Track(
        title=title,
        artists=list(artists),
        album="Album",
        isrc=None,
        duration_ms=200_000,
        track_number=1,
        disc_number=1,
        release_year=None,
        cover_art_url=None,
        source_id="sid12345",
    )


def _make_fake_ydl(recorder, *, behavior="success"):
    """Return a fake YoutubeDL class (a context manager) with configurable behavior."""

    class _FakeYDL:
        def __init__(self, opts):
            recorder["opts"] = opts
            recorder["constructed"] = recorder.get("constructed", 0) + 1

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            recorder["urls"] = urls
            if behavior == "success":
                outtmpl = recorder["opts"]["outtmpl"]
                codec = recorder["opts"]["postprocessors"][0]["preferredcodec"]
                base = outtmpl.replace(".%(ext)s", "")
                ext = _output_ext(codec)  # FFmpeg's real output ext (vorbis -> ogg, ...)
                Path(f"{base}.{ext}").write_text("fake audio")  # simulate transcode output
            elif behavior == "ytdlp_error":
                raise YtdlpDownloadError("HTTP Error 403: Forbidden")
            elif behavior == "ffmpeg_in_message":
                raise YtdlpDownloadError("Postprocessing: ffprobe and ffmpeg not found")
            elif behavior == "ffmpeg_transcode_error":
                raise YtdlpDownloadError("ffmpeg exited with code 1: Invalid data found")
            elif behavior == "video_unavailable":
                raise YtdlpDownloadError(
                    "ERROR: [youtube] dQw4w9WgXcQ: Video unavailable. This video is not available"
                )
            elif behavior == "no_output":
                pass  # claim success but write nothing

    return _FakeYDL


@pytest.fixture
def ffmpeg_present(mocker):
    """Simulate FFmpeg being installed so the proactive guard passes."""
    mocker.patch.object(mod, "which", return_value="/usr/bin/ffmpeg")


# -- idempotency ------------------------------------------------------------


def test_idempotent_skips_without_ffmpeg_or_ydl(tmp_path, mocker):
    track = _track()
    target = tmp_path / "Artist A - Song.mp3"
    target.write_text("already downloaded")
    # Even with NO ffmpeg present, an existing file is returned and yt-dlp is never built.
    mocker.patch.object(mod, "which", return_value=None)
    fake_ctor = mocker.patch.object(mod, "YoutubeDL")
    dl = YouTubeDownloader(tmp_path)

    result = dl.download("https://youtu.be/abc", track)

    assert result == str(target)
    fake_ctor.assert_not_called()


# -- happy path -------------------------------------------------------------


def test_happy_path_creates_and_returns_target(tmp_path, mocker, ffmpeg_present):
    recorder = {}
    mocker.patch.object(mod, "YoutubeDL", _make_fake_ydl(recorder))
    dl = YouTubeDownloader(tmp_path)

    result = dl.download("https://www.youtube.com/watch?v=abc", _track())

    expected = tmp_path / "Artist A - Song.mp3"
    assert result == str(expected)
    assert expected.exists()
    assert recorder["urls"] == ["https://www.youtube.com/watch?v=abc"]
    assert recorder["constructed"] == 1


# -- options ----------------------------------------------------------------


def test_options_are_correct(tmp_path, mocker, ffmpeg_present):
    recorder = {}
    mocker.patch.object(mod, "YoutubeDL", _make_fake_ydl(recorder))
    dl = YouTubeDownloader(tmp_path, audio_format="mp3", bitrate="320")

    dl.download("u", _track())

    opts = recorder["opts"]
    assert opts["format"] == "bestaudio/best"
    assert opts["noplaylist"] is True
    pp = opts["postprocessors"][0]
    assert pp["key"] == "FFmpegExtractAudio"
    assert pp["preferredcodec"] == "mp3"
    assert pp["preferredquality"] == "320"
    assert "cookiesfrombrowser" not in opts
    assert opts["outtmpl"] == str(tmp_path / "Artist A - Song") + ".%(ext)s"


def test_default_bitrate_is_192(tmp_path, mocker, ffmpeg_present):
    recorder = {}
    mocker.patch.object(mod, "YoutubeDL", _make_fake_ydl(recorder))
    YouTubeDownloader(tmp_path).download("u", _track())
    assert recorder["opts"]["postprocessors"][0]["preferredquality"] == "192"


def test_non_mp3_codec_uses_mapped_extension(tmp_path, mocker, ffmpeg_present):
    # vorbis transcodes to a .ogg file: target/idempotency path must use .ogg, not .vorbis.
    recorder = {}
    mocker.patch.object(mod, "YoutubeDL", _make_fake_ydl(recorder))
    dl = YouTubeDownloader(tmp_path, audio_format="vorbis")

    result = dl.download("u", _track())

    expected = tmp_path / "Artist A - Song.ogg"
    assert result == str(expected)
    assert expected.exists()
    assert recorder["opts"]["postprocessors"][0]["preferredcodec"] == "vorbis"


def test_cookies_from_browser_added(tmp_path, mocker, ffmpeg_present):
    recorder = {}
    mocker.patch.object(mod, "YoutubeDL", _make_fake_ydl(recorder))
    dl = YouTubeDownloader(tmp_path, cookies_from_browser="firefox")

    dl.download("u", _track())

    assert recorder["opts"]["cookiesfrombrowser"] == ("firefox",)


def test_no_cookies_key_when_none(tmp_path, mocker, ffmpeg_present):
    recorder = {}
    mocker.patch.object(mod, "YoutubeDL", _make_fake_ydl(recorder))

    YouTubeDownloader(tmp_path).download("u", _track())

    assert "cookiesfrombrowser" not in recorder["opts"]


# -- FFmpeg + failure mapping ----------------------------------------------


def test_ffmpeg_missing_proactive_guard(tmp_path, mocker):
    # shutil.which -> None: clean DownloadError and yt-dlp is NEVER constructed.
    mocker.patch.object(mod, "which", return_value=None)
    fake_ctor = mocker.patch.object(mod, "YoutubeDL")
    dl = YouTubeDownloader(tmp_path)

    with pytest.raises(DownloadError, match="FFmpeg is required"):
        dl.download("u", _track())
    fake_ctor.assert_not_called()


def test_ffmpeg_missing_message_fallback_in_except(tmp_path, mocker, ffmpeg_present):
    # which() passes, but yt-dlp still reports ffmpeg -> string fallback yields clean msg.
    recorder = {}
    mocker.patch.object(mod, "YoutubeDL", _make_fake_ydl(recorder, behavior="ffmpeg_in_message"))
    dl = YouTubeDownloader(tmp_path)

    with pytest.raises(DownloadError, match="FFmpeg is required"):
        dl.download("u", _track())


def test_ytdlp_error_wrapped_as_download_error(tmp_path, mocker, ffmpeg_present):
    recorder = {}
    mocker.patch.object(mod, "YoutubeDL", _make_fake_ydl(recorder, behavior="ytdlp_error"))
    dl = YouTubeDownloader(tmp_path)

    with pytest.raises(DownloadError):
        dl.download("u", _track())


def test_non_ytdlp_exception_is_also_wrapped(tmp_path, mocker, ffmpeg_present):
    # A non-YoutubeDLError (e.g. ValueError from an extractor) must not escape raw.
    fake = mocker.patch.object(mod, "YoutubeDL")
    ctx = fake.return_value
    ctx.__enter__.return_value = ctx
    ctx.__exit__.return_value = False  # must NOT suppress the exception
    ctx.download.side_effect = ValueError("boom")
    dl = YouTubeDownloader(tmp_path)

    # the raw ValueError is wrapped (message carries the original text), not the no-output path
    with pytest.raises(DownloadError, match="boom"):
        dl.download("u", _track())


def test_transcode_error_not_mislabeled_as_missing(tmp_path, mocker, ffmpeg_present):
    # ffmpeg present + a real transcode failure -> generic DownloadError, NOT the install msg.
    recorder = {}
    mocker.patch.object(
        mod, "YoutubeDL", _make_fake_ydl(recorder, behavior="ffmpeg_transcode_error")
    )
    dl = YouTubeDownloader(tmp_path)

    with pytest.raises(DownloadError) as excinfo:
        dl.download("u", _track())
    assert "FFmpeg is required" not in str(excinfo.value)


def test_video_unavailable_error_wrapped_as_download_error(tmp_path, mocker, ffmpeg_present):
    # yt-dlp's "This video is not available" must surface as a cratedig DownloadError so the
    # orchestrator can fall back to the next ranked candidate instead of aborting on a raw error.
    recorder = {}
    mocker.patch.object(mod, "YoutubeDL", _make_fake_ydl(recorder, behavior="video_unavailable"))
    dl = YouTubeDownloader(tmp_path)

    with pytest.raises(DownloadError, match="not available"):
        dl.download("https://www.youtube.com/watch?v=dQw4w9WgXcQ", _track())


def test_mkdir_failure_wrapped_as_download_error(tmp_path, mocker, ffmpeg_present):
    # A filesystem error creating the output dir must also be wrapped: it once ran outside the
    # try and escaped raw, bypassing the orchestrator's per-candidate DownloadError fallback.
    recorder = {}
    mocker.patch.object(mod, "YoutubeDL", _make_fake_ydl(recorder))
    mocker.patch.object(Path, "mkdir", side_effect=OSError("permission denied"))
    dl = YouTubeDownloader(tmp_path / "nested")

    with pytest.raises(DownloadError, match="permission denied"):
        dl.download("u", _track())
    assert "constructed" not in recorder  # failed before yt-dlp was ever built


def test_target_exists_oserror_wrapped_as_download_error(tmp_path, mocker, ffmpeg_present):
    # The idempotency probe (target.exists()) must also honor download()'s DownloadError-only
    # contract: an EACCES-style OSError from stat() is wrapped, not leaked raw past the fallback.
    recorder = {}
    mocker.patch.object(mod, "YoutubeDL", _make_fake_ydl(recorder))
    mocker.patch.object(Path, "exists", side_effect=OSError("permission denied"))
    dl = YouTubeDownloader(tmp_path)

    with pytest.raises(DownloadError, match="permission denied"):
        dl.download("u", _track())
    assert "constructed" not in recorder  # failed before yt-dlp was ever built


def test_missing_output_after_success_wrapped(tmp_path, mocker, ffmpeg_present):
    recorder = {}
    mocker.patch.object(mod, "YoutubeDL", _make_fake_ydl(recorder, behavior="no_output"))
    dl = YouTubeDownloader(tmp_path)

    with pytest.raises(DownloadError):
        dl.download("u", _track())


# -- _safe_filename (pure) --------------------------------------------------


def test_safe_filename_sanitizes_illegal_chars():
    track = _track(title='A:B/C?"D"<E>|F*', artists=("Ar\\tist",))
    name = _safe_filename(track, "mp3")
    assert name.endswith(".mp3")
    for ch in '<>:"/\\|?*':
        assert ch not in name
    assert _safe_filename(track, "mp3") == name  # deterministic


def test_safe_filename_example():
    track = _track(title="Song: Part 2?", artists=("AC/DC",))
    assert _safe_filename(track, "mp3") == "AC DC - Song Part 2.mp3"


def test_safe_filename_strips_trailing_dots_and_spaces():
    track = _track(title="Title... ", artists=("Artist",))
    assert _safe_filename(track, "mp3") == "Artist - Title.mp3"


def test_safe_filename_falls_back_when_empty():
    track = _track(title="???", artists=("///",))
    name = _safe_filename(track, "mp3")
    assert name == "sid12345.mp3"


def test_safe_filename_strips_control_chars():
    track = _track(title="A\x7fB\x85C", artists=("Artist",))
    name = _safe_filename(track, "mp3")
    assert "\x7f" not in name
    assert "\x85" not in name
