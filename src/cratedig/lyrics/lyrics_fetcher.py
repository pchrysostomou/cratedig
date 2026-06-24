# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""Lyrics fetcher (Phase 4) — LRCLIB lookup, soft-fail.

Stub — implemented in a later phase. Tries ``/api/get`` (title + primary artist +
album + duration), falls back to ``/api/search``. Soft-fail: returns ``None`` on
any miss/error/timeout and never raises into the pipeline. See DESIGN.md §6.
"""

# TODO: implement fetch_lyrics(track, timeout=10.0) -> str | None  (must not raise).
