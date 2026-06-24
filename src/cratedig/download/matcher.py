# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""YouTube matcher (Phase 2) — normalize both sides, then score candidates.

Stub — implemented in a later phase. Normalizes titles/artists, then scores
candidates with duration as the heaviest weight plus fuzzy title/artist
similarity (rapidfuzz), using ISRC where available. See DESIGN.md §6.
"""

# TODO: implement find_best_match(track, ydl) -> str | None.
