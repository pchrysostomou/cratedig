"""Tests for the Typer CLI (collaborators mocked; no real network)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from cratedig import __version__, cli
from cratedig.cli import app
from cratedig.config import Settings
from cratedig.exceptions import InvalidUrlError, ProviderApiError
from cratedig.models import DownloadResult, ResultStatus, Track

runner = CliRunner()


def _track(title, artist="Artist"):
    return Track(
        title=title,
        artists=[artist],
        album="Album",
        isrc=None,
        duration_ms=1000,
        track_number=1,
        disc_number=1,
        release_year=None,
        cover_art_url=None,
        source_id="sid",
    )


@pytest.fixture
def wired(mocker, tmp_path):
    """Patch collaborators so `download` runs offline; return the Orchestrator mock."""
    mocker.patch.object(cli, "get_settings", return_value=Settings(output_dir=tmp_path))
    mocker.patch.object(cli, "MusicBrainzHandler")
    mocker.patch.object(cli, "YouTubeDownloader")
    mocker.patch.object(cli, "Tagger")
    mocker.patch.object(cli, "YoutubeDL")
    return mocker.patch.object(cli, "Orchestrator")


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_summary_table_and_tally(wired):
    wired.return_value.run.return_value = [
        DownloadResult(track=_track("Song A"), status=ResultStatus.SUCCESS, lyrics_found=True),
        DownloadResult(track=_track("Song B"), status=ResultStatus.SKIPPED),
        DownloadResult(track=_track("Song C"), status=ResultStatus.NOT_FOUND),
        DownloadResult(track=_track("Song D"), status=ResultStatus.FAILED, error="x"),
    ]

    result = runner.invoke(app, ["download", "midnight city"])

    assert result.exit_code == 0
    assert "1 downloaded, 1 skipped, 1 not found, 1 failed" in result.output


def test_summary_table_contents(wired):
    wired.return_value.run.return_value = [
        DownloadResult(track=_track("Alpha"), status=ResultStatus.SUCCESS, lyrics_found=True),
        DownloadResult(track=_track("Beta"), status=ResultStatus.NOT_FOUND, lyrics_found=False),
    ]
    result = runner.invoke(app, ["download", "some album"])
    assert result.exit_code == 0
    out = result.output
    assert "Track" in out and "Status" in out and "Lyrics" in out  # column headers
    assert "Alpha" in out and "Beta" in out  # per-row track names


def test_no_tracks_found_is_clean_exit_zero(wired):
    wired.return_value.run.return_value = []  # empty -> friendly message, exit 0
    result = runner.invoke(app, ["download", "no such song"])
    assert result.exit_code == 0
    assert "No tracks found" in result.output


def test_no_lyrics_passes_null_fetcher(wired):
    wired.return_value.run.return_value = []
    result = runner.invoke(app, ["download", "midnight city", "--no-lyrics"])
    assert result.exit_code == 0
    assert wired.call_args.kwargs["lyrics_fetcher"] is None


def test_lyrics_fetcher_used_without_flag(wired):
    wired.return_value.run.return_value = []
    runner.invoke(app, ["download", "midnight city"])
    assert wired.call_args.kwargs["lyrics_fetcher"] is cli.fetch_lyrics


def test_invalid_url_clean_exit(wired):
    wired.return_value.run.side_effect = InvalidUrlError("Unsupported MusicBrainz entity")
    result = runner.invoke(app, ["download", "https://musicbrainz.org/artist/x"])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Unsupported" in result.output


def test_provider_api_error_clean_exit(wired):
    wired.return_value.run.side_effect = ProviderApiError("MusicBrainz request failed")
    result = runner.invoke(app, ["download", "midnight city"])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "MusicBrainz request failed" in result.output


def test_cli_overrides_mapping_and_precedence(mocker, tmp_path):
    # --workers maps to max_workers; omitted flags pass None so .env/defaults win.
    captured = {}

    def fake_get_settings(**overrides):
        captured.update(overrides)
        return Settings(output_dir=tmp_path)

    mocker.patch.object(cli, "get_settings", side_effect=fake_get_settings)
    mocker.patch.object(cli, "MusicBrainzHandler")
    mocker.patch.object(cli, "YouTubeDownloader")
    mocker.patch.object(cli, "Tagger")
    mocker.patch.object(cli, "YoutubeDL")
    orch = mocker.patch.object(cli, "Orchestrator")
    orch.return_value.run.return_value = []

    runner.invoke(app, ["download", "midnight city", "--workers", "7", "--format", "m4a"])

    assert captured["max_workers"] == 7  # --workers -> max_workers
    assert captured["audio_format"] == "m4a"
    assert captured["bitrate"] is None  # omitted -> None (env/default wins)
    assert captured["output_dir"] is None
    assert captured["cookies_from_browser"] is None


def test_search_ydl_built_and_passed_to_orchestrator(wired):
    wired.return_value.run.return_value = []
    runner.invoke(app, ["download", "midnight city"])
    assert cli.YoutubeDL.called  # a dedicated search YoutubeDL is constructed
    assert wired.call_args.kwargs["ydl"] is cli.YoutubeDL.return_value
