"""Tests for config.Settings / get_settings (keyless; no real network)."""

from __future__ import annotations

import pytest

from cratedig.config import get_settings

_ENV_VARS = ["OUTPUT_DIR", "AUDIO_FORMAT", "BITRATE", "MAX_WORKERS", "COOKIES_FROM_BROWSER"]


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    # Empty cwd so no stray .env is loaded; clear any ambient settings env vars.
    monkeypatch.chdir(tmp_path)
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_defaults():
    settings = get_settings()
    assert settings.audio_format == "mp3"
    assert settings.bitrate == "192"
    assert settings.max_workers == 3
    assert settings.cookies_from_browser is None


def test_no_credentials_required():
    # MusicBrainz is keyless: building settings never requires/raises on credentials.
    settings = get_settings()
    assert not hasattr(settings, "spotify_client_id")


def test_env_populates_settings(monkeypatch):
    monkeypatch.setenv("AUDIO_FORMAT", "opus")
    monkeypatch.setenv("MAX_WORKERS", "5")

    settings = get_settings()

    assert settings.audio_format == "opus"
    assert settings.max_workers == 5


def test_cli_overrides_beat_env_and_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIO_FORMAT", "flac")  # env value...

    settings = get_settings(
        output_dir=tmp_path,
        audio_format="opus",  # ...overridden by the CLI
        bitrate="320",
        max_workers=1,
        cookies_from_browser="firefox",
    )

    assert settings.output_dir == tmp_path
    assert settings.audio_format == "opus"
    assert settings.bitrate == "320"
    assert settings.max_workers == 1
    assert settings.cookies_from_browser == "firefox"


def test_none_overrides_are_ignored(monkeypatch):
    monkeypatch.setenv("AUDIO_FORMAT", "flac")

    settings = get_settings(audio_format=None, bitrate=None)

    assert settings.audio_format == "flac"  # None override dropped -> env wins
    assert settings.bitrate == "192"  # None override dropped -> built-in default
