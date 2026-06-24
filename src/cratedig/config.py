# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""Configuration (Pydantic settings). See DESIGN.md §9.

MusicBrainz is keyless, so there are no credentials here — only output and
download defaults that the CLI can override.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, populated from env / .env with overridable defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    output_dir: Path = Field(default_factory=lambda: Path.home() / "Music" / "cratedig")
    audio_format: str = "mp3"
    bitrate: str = "192"
    max_workers: int = 3
    cookies_from_browser: str | None = None


def get_settings(**overrides: object) -> Settings:
    """Build ``Settings`` from env/.env plus CLI ``overrides`` (which take priority).

    ``None`` overrides are dropped so an unspecified CLI flag falls back to the
    env/.env value or the built-in default.
    """
    provided = {key: value for key, value in overrides.items() if value is not None}
    return Settings(**provided)
