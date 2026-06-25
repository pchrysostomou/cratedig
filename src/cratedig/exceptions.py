# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""Custom exception hierarchy (see DESIGN.md §7).

Strategy: fail fast on config/provider, fail soft per track. Lyrics never raise
into the pipeline (failures are swallowed inside ``lyrics_fetcher``), so there is
no lyrics-specific exception here.
"""


class CratedigError(Exception):
    """Base class for all cratedig errors."""


class ConfigError(CratedigError):
    """Missing or invalid configuration (e.g. a bad value in .env or a CLI flag). Fatal."""


class ProviderError(CratedigError):
    """Base for metadata-provider failures."""


class InvalidUrlError(ProviderError):
    """The supplied input is not a recognized provider URL / ID."""


class ProviderApiError(ProviderError):
    """Metadata-provider API failure (network / 4xx / 5xx) — fatal for the run."""


class MatchNotFoundError(CratedigError):
    """No acceptable YouTube match was found for a track — per-track NOT_FOUND."""


class DownloadError(CratedigError):
    """yt-dlp / FFmpeg download or transcode failure — per-track FAILED."""


class TaggingError(CratedigError):
    """Failed to write tags / embed art or lyrics — per-track FAILED (file kept)."""
