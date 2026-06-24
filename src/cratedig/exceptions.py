# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""Custom exception hierarchy (see DESIGN.md §7).

Strategy: fail fast on config, fail soft per track. Lyrics never raise into the
pipeline (failures are swallowed inside ``lyrics_fetcher``), so there is no
lyrics-specific exception here.
"""


class CratedigError(Exception):
    """Base class for all cratedig errors."""


class ConfigError(CratedigError):
    """Missing/invalid configuration (e.g. Spotify creds). Fatal — exit early."""


class SpotifyError(CratedigError):
    """Base for Spotify-related failures."""


class InvalidUrlError(SpotifyError):
    """The supplied URL/URI is not a valid Spotify track/album/playlist."""


class SpotifyApiError(SpotifyError):
    """Spotify API failure (401/429/5xx) — backoff, then fatal."""


class MatchNotFoundError(CratedigError):
    """No acceptable YouTube match was found for a track — per-track NOT_FOUND."""


class DownloadError(CratedigError):
    """yt-dlp / FFmpeg download or transcode failure — per-track FAILED."""


class TaggingError(CratedigError):
    """Failed to write tags / embed art or lyrics — per-track FAILED (file kept)."""
