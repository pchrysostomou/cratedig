# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""The data contract: ``Track``, ``ResultStatus``, ``DownloadResult``.

See DESIGN.md §5. ``Track`` is frozen (it is a metadata contract); lyrics are
fetched *after* construction, so the orchestrator produces an enriched copy via
``dataclasses.replace(track, lyrics=...)`` rather than mutating in place.
"""

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True, slots=True)
class Track:
    title: str
    artists: list[str]  # ordered; artists[0] is primary
    album: str
    isrc: str | None  # strong signal for matching
    duration_ms: int  # used by BOTH the YT matcher and LRCLIB
    track_number: int
    disc_number: int
    release_year: str | None
    cover_art_url: str | None
    source_id: str
    lyrics: str | None = None  # enriched post-fetch via dataclasses.replace()

    @property
    def primary_artist(self) -> str:
        return self.artists[0] if self.artists else "Unknown Artist"

    @property
    def search_query(self) -> str:
        return f"{self.primary_artist} - {self.title}"


class ResultStatus(str, Enum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    NOT_FOUND = "not_found"
    FAILED = "failed"


@dataclass
class DownloadResult:
    track: Track
    status: ResultStatus
    output_path: str | None = None
    youtube_url: str | None = None
    lyrics_found: bool = False  # surfaced in the summary table
    error: str | None = None
