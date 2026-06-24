# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""Pipeline orchestration (Phase 5) — single-threaded, end-to-end.

Owns the per-track pipeline (match -> download -> enrich with lyrics -> tag),
isolating every per-track failure into a ``DownloadResult`` so one bad track
never aborts the batch. Concurrency (the thread pool) is Phase 6.
See DESIGN.md §4, §6, §7.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from pathlib import Path

from cratedig.download import youtube_downloader as _ytdl
from cratedig.exceptions import DownloadError, TaggingError
from cratedig.models import DownloadResult, ResultStatus, Track

logger = logging.getLogger(__name__)


class Orchestrator:
    """Runs the metadata -> match -> download -> tag pipeline for a source input.

    Collaborators are injected so the pipeline is unit-testable with fakes:
    ``matcher`` is a callable ``(track, ydl) -> str | None`` and ``lyrics_fetcher``
    a callable ``(track) -> str | None`` (or ``None`` to skip lyrics, e.g. ``--no-lyrics``).
    """

    def __init__(
        self,
        handler,
        downloader,
        matcher: Callable,
        lyrics_fetcher: Callable | None,
        tagger,
        ydl,
        max_workers: int = 3,
    ) -> None:
        self.handler = handler
        self.downloader = downloader
        self.matcher = matcher
        self.lyrics_fetcher = lyrics_fetcher
        self.tagger = tagger
        self.ydl = ydl
        self.max_workers = max_workers  # stored; the thread pool is Phase 6

    def run(self, url: str) -> list[DownloadResult]:
        # Fetch-level failures (InvalidUrlError / ProviderApiError) are fatal for the
        # whole run: they propagate out so the CLI can show one clean error.
        tracks = self.handler.fetch(url)
        return [self._process(track) for track in tracks]

    def _process(self, track: Track) -> DownloadResult:
        try:
            video_url = self.matcher(track, self.ydl)
            if video_url is None:
                return DownloadResult(
                    track=track,
                    status=ResultStatus.NOT_FOUND,
                    error="No acceptable YouTube match found.",
                )

            target = self._expected_path(track)
            if target is not None and target.exists():
                return DownloadResult(
                    track=track,
                    status=ResultStatus.SKIPPED,
                    output_path=str(target),
                    youtube_url=video_url,
                )

            try:
                path = self.downloader.download(video_url, track)
            except DownloadError as exc:
                return DownloadResult(
                    track=track,
                    status=ResultStatus.FAILED,
                    youtube_url=video_url,
                    error=str(exc),
                )

            lyrics = self._fetch_lyrics(track)
            enriched = dataclasses.replace(track, lyrics=lyrics)

            try:
                self.tagger.tag(path, enriched)
            except TaggingError as exc:
                # Per §7 the audio file is kept; the download itself succeeded.
                return DownloadResult(
                    track=track,
                    status=ResultStatus.FAILED,
                    output_path=path,
                    youtube_url=video_url,
                    lyrics_found=bool(lyrics),
                    error=f"Tagging failed (file kept): {exc}",
                )

            return DownloadResult(
                track=track,
                status=ResultStatus.SUCCESS,
                output_path=path,
                youtube_url=video_url,
                lyrics_found=bool(lyrics),
            )
        except Exception as exc:  # per-track isolation: one bad track never aborts the batch
            logger.warning("Unexpected error processing %s: %s", track.search_query, exc)
            return DownloadResult(track=track, status=ResultStatus.FAILED, error=str(exc))

    def _fetch_lyrics(self, track: Track) -> str | None:
        if self.lyrics_fetcher is None:
            return None
        try:
            return self.lyrics_fetcher(track)
        except Exception:  # lyrics are soft-fail and never abort a track (DESIGN.md §7)
            return None

    def _expected_path(self, track: Track) -> Path | None:
        # Skip detection (Option A): reuse the downloader's canonical naming so this path
        # is byte-identical to what download() produces (locked by a regression test).
        try:
            ext = _ytdl._output_ext(self.downloader.audio_format)
            return Path(self.downloader.output_dir) / _ytdl._safe_filename(track, ext)
        except Exception:
            return None  # cannot compute -> just don't treat it as a skip
