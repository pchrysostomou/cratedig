"""Phase 5 tests for the Orchestrator (collaborators injected as fakes; no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cratedig.core.orchestrator import Orchestrator
from cratedig.download.youtube_downloader import _output_ext, _safe_filename
from cratedig.exceptions import DownloadError, ProviderApiError, TaggingError
from cratedig.models import ResultStatus, Track


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
        source_id="sid",
    )


class _FakeHandler:
    def __init__(self, tracks=(), error=None):
        self._tracks = list(tracks)
        self._error = error

    def fetch(self, url):
        if self._error is not None:
            raise self._error
        return list(self._tracks)


class _FakeDownloader:
    def __init__(self, output_dir, audio_format="mp3", behavior="ok"):
        self.output_dir = Path(output_dir)
        self.audio_format = audio_format
        self.behavior = behavior
        self.calls = []

    def download(self, video_url, track):
        self.calls.append((video_url, track))
        if self.behavior == "download_error":
            raise DownloadError("yt-dlp failed")
        if self.behavior == "boom":
            raise RuntimeError("unexpected downloader crash")
        path = self.output_dir / _safe_filename(track, _output_ext(self.audio_format))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("audio")
        return str(path)


class _FakeTagger:
    def __init__(self, behavior="ok"):
        self.behavior = behavior
        self.tagged = []

    def tag(self, file_path, track):
        self.tagged.append((file_path, track))
        if self.behavior == "tagging_error":
            raise TaggingError("bad tags")


def _match_ok(track, ydl):
    return f"https://www.youtube.com/watch?v={track.source_id}"


def _match_none(track, ydl):
    return None


def _orch(handler, downloader, *, matcher=_match_ok, lyrics_fetcher=None, tagger=None):
    return Orchestrator(
        handler=handler,
        downloader=downloader,
        matcher=matcher,
        lyrics_fetcher=lyrics_fetcher,
        tagger=tagger or _FakeTagger(),
        ydl=object(),
    )


# -- happy path -------------------------------------------------------------


def test_happy_path_all_success_with_lyrics(tmp_path):
    tracks = [_track(title="A"), _track(title="B")]
    tagger = _FakeTagger()
    orch = _orch(
        _FakeHandler(tracks),
        _FakeDownloader(tmp_path),
        lyrics_fetcher=lambda t: "la la",
        tagger=tagger,
    )

    results = orch.run("some album")

    assert [r.status for r in results] == [ResultStatus.SUCCESS, ResultStatus.SUCCESS]
    assert all(r.lyrics_found for r in results)
    assert all(r.output_path for r in results)
    # the replace()-enriched track (lyrics set) is what reaches the tagger
    assert all(tagged_track.lyrics == "la la" for _, tagged_track in tagger.tagged)


def test_no_lyrics_fetcher_means_no_lyrics(tmp_path):
    tagger = _FakeTagger()
    orch = _orch(
        _FakeHandler([_track()]), _FakeDownloader(tmp_path), lyrics_fetcher=None, tagger=tagger
    )

    results = orch.run("u")

    assert results[0].status == ResultStatus.SUCCESS
    assert results[0].lyrics_found is False
    assert tagger.tagged[0][1].lyrics is None


# -- per-status outcomes ----------------------------------------------------


def test_matcher_none_is_not_found(tmp_path):
    downloader = _FakeDownloader(tmp_path)
    orch = _orch(_FakeHandler([_track()]), downloader, matcher=_match_none)

    results = orch.run("u")

    assert results[0].status == ResultStatus.NOT_FOUND
    assert downloader.calls == []  # never attempted a download


def test_download_error_is_failed(tmp_path):
    orch = _orch(_FakeHandler([_track()]), _FakeDownloader(tmp_path, behavior="download_error"))
    results = orch.run("u")
    assert results[0].status == ResultStatus.FAILED
    assert "yt-dlp failed" in results[0].error


def test_tagging_error_is_failed_but_file_kept(tmp_path):
    orch = _orch(
        _FakeHandler([_track()]),
        _FakeDownloader(tmp_path),
        lyrics_fetcher=lambda t: "x",
        tagger=_FakeTagger(behavior="tagging_error"),
    )
    result = orch.run("u")[0]
    assert result.status == ResultStatus.FAILED
    assert result.output_path is not None and Path(result.output_path).exists()  # file kept


def test_idempotent_skip(tmp_path):
    track = _track()
    downloader = _FakeDownloader(tmp_path)
    existing = tmp_path / _safe_filename(track, _output_ext("mp3"))
    existing.write_text("already here")  # canonical target already present
    orch = _orch(_FakeHandler([track]), downloader)

    results = orch.run("u")

    assert results[0].status == ResultStatus.SKIPPED
    assert results[0].output_path == str(existing)
    assert downloader.calls == []  # skip => no download call


# -- isolation + fatal ------------------------------------------------------


def test_unexpected_downloader_error_is_failed(tmp_path):
    # a non-DownloadError from the downloader is caught by the outer handler -> FAILED
    orch = _orch(_FakeHandler([_track()]), _FakeDownloader(tmp_path, behavior="boom"))
    result = orch.run("u")[0]
    assert result.status == ResultStatus.FAILED
    assert "unexpected downloader crash" in result.error


def test_unexpected_matcher_error_is_failed(tmp_path):
    def boom_matcher(track, ydl):
        raise RuntimeError("matcher exploded")

    orch = _orch(_FakeHandler([_track()]), _FakeDownloader(tmp_path), matcher=boom_matcher)
    result = orch.run("u")[0]
    assert result.status == ResultStatus.FAILED
    assert "matcher exploded" in result.error


def test_keyboard_interrupt_propagates(tmp_path):
    # BaseException (Ctrl-C) must NOT be swallowed by the per-track isolation handler.
    def interrupting_matcher(track, ydl):
        raise KeyboardInterrupt

    orch = _orch(_FakeHandler([_track()]), _FakeDownloader(tmp_path), matcher=interrupting_matcher)
    with pytest.raises(KeyboardInterrupt):
        orch.run("u")


def test_max_workers_stored_but_inert(tmp_path):
    # Phase 5 is single-threaded; max_workers is stored (default 3) but not yet used.
    assert _orch(_FakeHandler([]), _FakeDownloader(tmp_path)).max_workers == 3
    orch = Orchestrator(
        handler=_FakeHandler([]),
        downloader=_FakeDownloader(tmp_path),
        matcher=_match_ok,
        lyrics_fetcher=None,
        tagger=_FakeTagger(),
        ydl=object(),
        max_workers=7,
    )
    assert orch.max_workers == 7


def test_per_track_isolation(tmp_path):
    class _SelectiveTagger:
        def __init__(self):
            self.tagged = []

        def tag(self, file_path, track):
            if track.title == "Bad":
                raise RuntimeError("boom mid-pipeline")  # not a TaggingError
            self.tagged.append(track)

    tracks = [_track(title="Good"), _track(title="Bad"), _track(title="Good2")]
    orch = _orch(_FakeHandler(tracks), _FakeDownloader(tmp_path), tagger=_SelectiveTagger())

    results = orch.run("u")

    assert [r.status for r in results] == [
        ResultStatus.SUCCESS,
        ResultStatus.FAILED,
        ResultStatus.SUCCESS,
    ]
    assert "boom mid-pipeline" in results[1].error


def test_fetch_level_error_propagates(tmp_path):
    orch = _orch(_FakeHandler(error=ProviderApiError("api down")), _FakeDownloader(tmp_path))
    with pytest.raises(ProviderApiError):
        orch.run("u")


def test_lyrics_callable_raising_is_soft(tmp_path):
    def boom_lyrics(track):
        raise RuntimeError("lrclib exploded")

    tagger = _FakeTagger()
    orch = _orch(
        _FakeHandler([_track()]),
        _FakeDownloader(tmp_path),
        lyrics_fetcher=boom_lyrics,
        tagger=tagger,
    )

    results = orch.run("u")

    assert results[0].status == ResultStatus.SUCCESS  # lyrics failure never fails the track
    assert results[0].lyrics_found is False
    assert tagger.tagged[0][1].lyrics is None


# -- regression: skip path == real downloader output (Option A drift guard) -


@pytest.mark.parametrize("audio_format", ["mp3", "vorbis", "aac", "alac"])
@pytest.mark.parametrize(
    "track",
    [
        _track(title="Sōng: Test? / Mix", artists=("AC/DC",)),  # unicode + illegal chars
        _track(title="???", artists=("///",)),  # all-illegal -> source_id fallback
        _track(title="a.mp3", artists=("b",)),  # dotted base (Path.with_suffix trap)
        _track(title="X" * 250, artists=("Y",)),  # length cap
    ],
)
def test_skip_path_matches_real_downloader_output(track, audio_format, tmp_path, mocker):
    """The orchestrator's skip path must be byte-identical to download()'s real output.

    Drives a REAL YouTubeDownloader (fake YoutubeDL creates the file) across the
    codec->ext-divergent formats and adversarial filenames where _safe_filename /
    _output_ext / _build_opts(with_suffix) could silently drift.
    """
    from cratedig.download import youtube_downloader as ytdl_mod

    class _CreatingYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            base = self.opts["outtmpl"].replace(".%(ext)s", "")
            codec = self.opts["postprocessors"][0]["preferredcodec"]
            Path(f"{base}.{ytdl_mod._output_ext(codec)}").write_text("audio")

    mocker.patch.object(ytdl_mod, "which", return_value="/usr/bin/ffmpeg")
    mocker.patch.object(ytdl_mod, "YoutubeDL", _CreatingYDL)
    real_downloader = ytdl_mod.YouTubeDownloader(tmp_path, audio_format=audio_format)
    returned = real_downloader.download("http://x", track)

    orch = _orch(_FakeHandler([]), real_downloader)
    assert str(orch._expected_path(track)) == returned
