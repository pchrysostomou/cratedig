# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""Configuration & secrets (Pydantic settings). See DESIGN.md §9.

Spotify credentials are loaded from the environment / a local ``.env`` (never
bundled). Other knobs have sensible defaults that the CLI can override. Missing
credentials raise ``ConfigError`` (fatal, surfaced cleanly by the CLI).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from cratedig.exceptions import ConfigError


class Settings(BaseSettings):
    """Runtime configuration, populated from env / .env with overridable defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    output_dir: Path = Field(default_factory=lambda: Path.home() / "Music" / "cratedig")
    audio_format: str = "mp3"
    bitrate: str = "192"
    max_workers: int = 3
    cookies_from_browser: str | None = None


def get_settings(**overrides: object) -> Settings:
    """Build ``Settings`` from env/.env plus CLI ``overrides`` (which take priority).

    ``None`` overrides are dropped so an unspecified CLI flag falls back to the
    env/.env value or the built-in default. Raises ``ConfigError`` if either
    Spotify credential is missing.
    """
    provided = {key: value for key, value in overrides.items() if value is not None}
    settings = Settings(**provided)
    if not settings.spotify_client_id or not settings.spotify_client_secret:
        raise ConfigError(
            "Spotify credentials are missing. Set SPOTIFY_CLIENT_ID and "
            "SPOTIFY_CLIENT_SECRET in your environment or a .env file "
            "(create an app at https://developer.spotify.com/dashboard)."
        )
    return settings
