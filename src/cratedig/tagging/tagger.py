# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""Audio tagger (Phase 4) — ID3 tags + cover art + USLT lyrics.

Stub — implemented in a later phase. Writes text frames + APIC cover art and, if
present, embeds ``track.lyrics`` as a USLT frame. Each step is independently
guarded so one failure doesn't lose the others. See DESIGN.md §6.
"""

# TODO: implement Tagger().tag(file_path, track) -> None.
