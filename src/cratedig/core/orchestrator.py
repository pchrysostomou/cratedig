# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""Pipeline orchestration — concurrent, end-to-end (Phase 6).

Owns the per-track pipeline (match -> download -> enrich with lyrics -> tag),
isolating every per-track failure into a ``DownloadResult`` so one bad track
never aborts the batch. Tracks download in parallel via a ``ThreadPoolExecutor``
(``max_workers``); metadata is fetched sequentially first so the MusicBrainz
rate limiter is unaffected. See DESIGN.md §4, §6, §7, §8.
"""

from __future__ import annotations

import dataclasses
import logging
import random
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from cratedig.download import youtube_downloader as _ytdl
from cratedig.exceptions import DownloadError, TaggingError
from cratedig.models import DownloadResult, ResultStatus, Track

logger = logging.getLogger(__name__)

# Per-worker startup jitter (DESIGN.md §8): a small random delay before each track hits YouTube,
# so concurrent workers don't all fire at once and trip anti-bot heuristics. Patchable in tests.
_JITTER_MIN_S = 0.5
_JITTER_MAX_S = 2.0


class Orchestrator:
    """Runs the metadata -> match -> download -> tag pipeline for a source input.

    Collaborators are injected so the pipeline is unit-testable with fakes:
    ``ranker`` is a callable ``(track, ydl) -> list[str]`` (best-first candidate watch URLs)
    and ``lyrics_fetcher`` a callable ``(track) -> str | None`` (or ``None`` to skip lyrics,
    e.g. ``--no-lyrics``).
    """

    def __init__(
        self,
        handler,
        downloader,
        ranker: Callable,
        lyrics_fetcher: Callable | None,
        tagger,
        ydl,
        max_workers: int = 3,
    ) -> None:
        self.handler = handler
        self.downloader = downloader
        self.ranker = ranker
        self.lyrics_fetcher = lyrics_fetcher
        self.tagger = tagger
        self.ydl = ydl
        self.max_workers = max_workers  # drives the ThreadPoolExecutor in run()

    def run(
        self,
        url: str,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[DownloadResult]:
        # Fetch-level failures (InvalidUrlError / ProviderApiError) are fatal for the whole run:
        # they propagate so the CLI can show one clean error. fetch() runs sequentially here, so
        # the MusicBrainz handler's ~1 req/s limiter is unaffected by the pool below.
        tracks = self.handler.fetch(url)
        if not tracks:
            return []

        # Phase 6: download tracks concurrently. Per-track isolation lives in _process
        # (except Exception -> FAILED), so a worker never aborts the batch. Results are collected
        # by index and returned in INPUT order (not completion order) for a stable summary.
        # ``on_progress(done, total)`` is invoked here on the main thread as each future
        # completes, so there are no cross-thread UI calls.
        total = len(tracks)
        by_index: dict[int, DownloadResult] = {}
        pool = ThreadPoolExecutor(max_workers=self.max_workers)
        try:
            future_to_index = {
                pool.submit(self._process, track): idx for idx, track in enumerate(tracks)
            }
            for future in as_completed(future_to_index):
                by_index[future_to_index[future]] = future.result()
                if on_progress is not None:
                    on_progress(len(by_index), total)
        except BaseException:
            # Ctrl-C (KeyboardInterrupt) or any fatal error: cancel PENDING work and propagate.
            # KeyboardInterrupt is a BaseException, so _process's per-track `except Exception`
            # never swallows it as a FAILED result — it surfaces here and aborts the run.
            # Already-running workers can't be force-stopped; they finish their current step.
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            pool.shutdown(wait=True)

        return [by_index[idx] for idx in range(total)]

    def _process(self, track: Track) -> DownloadResult:
        try:
            # Stagger workers so N threads don't hit YouTube at the same instant (anti-bot).
            time.sleep(random.uniform(_JITTER_MIN_S, _JITTER_MAX_S))
            candidates = self.ranker(track, self.ydl)
            if not candidates:
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
                    youtube_url=candidates[0],
                )

            # Try candidates best-first; fall back to the next on a download failure
            # (e.g. "video not available", 403, region-locked). First success wins.
            path: str | None = None
            chosen_url: str | None = None
            last_error: str | None = None
            for video_url in candidates:
                try:
                    path = self.downloader.download(video_url, track)
                    chosen_url = video_url
                    break
                except DownloadError as exc:
                    last_error = str(exc)
                    logger.info(
                        "download failed for %s: %s — trying next candidate", video_url, exc
                    )

            if path is None:  # every candidate failed to download
                return DownloadResult(
                    track=track,
                    status=ResultStatus.FAILED,
                    youtube_url=candidates[0],
                    error=last_error,
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
                    youtube_url=chosen_url,
                    lyrics_found=bool(lyrics),
                    error=f"Tagging failed (file kept): {exc}",
                )

            return DownloadResult(
                track=track,
                status=ResultStatus.SUCCESS,
                output_path=path,
                youtube_url=chosen_url,
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
