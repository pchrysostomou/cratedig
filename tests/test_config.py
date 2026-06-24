"""Phase 5 tests for config.Settings / get_settings (no real network)."""

from __future__ import annotations

import pytest

from cratedig.config import get_settings
from cratedig.exceptions import ConfigError

_ENV_VARS = [
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "OUTPUT_DIR",
    "AUDIO_FORMAT",
    "BITRATE",
    "MAX_WORKERS",
    "COOKIES_FROM_BROWSER",
]


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    # Empty cwd so no stray .env is loaded; clear any ambient settings env vars.
    monkeypatch.chdir(tmp_path)
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_env_credentials_populate_settings(monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id-123")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret-456")

    settings = get_settings()

    assert settings.spotify_client_id == "id-123"
    assert settings.spotify_client_secret == "secret-456"
    assert settings.audio_format == "mp3"  # built-in default
    assert settings.max_workers == 3


def test_missing_credentials_raise_config_error():
    with pytest.raises(ConfigError):
        get_settings()


def test_partial_credentials_raise_config_error(monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id-only")  # secret still missing
    with pytest.raises(ConfigError):
        get_settings()


def test_cli_overrides_beat_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "sec")

    settings = get_settings(
        output_dir=tmp_path,
        audio_format="opus",
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
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "sec")

    settings = get_settings(audio_format=None, bitrate=None)

    assert settings.audio_format == "mp3"  # None override dropped -> default
    assert settings.bitrate == "192"
