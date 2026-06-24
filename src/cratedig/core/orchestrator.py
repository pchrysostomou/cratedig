# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""Pipeline orchestration + concurrency (Phase 5/6).

Stub — implemented in a later phase. Will own the thread pool and the per-track
pipeline (match → download → fetch lyrics → enrich via ``replace()`` → tag),
catching every per-track exception into a ``DownloadResult``. See DESIGN.md §6.
"""

# TODO: implement Orchestrator(handler, downloader, lyrics_fetcher, tagger, max_workers).
