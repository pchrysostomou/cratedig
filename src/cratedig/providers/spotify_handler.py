# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""Spotify metadata provider (Phase 1).

Stub — implemented in a later phase. Client-Credentials auth (read-only catalog);
detects track/album/playlist and returns a normalized ``list[Track]``, batching
album/playlist pagination. See DESIGN.md §6.
"""

# TODO: implement SpotifyHandler(client_id, client_secret).fetch(url_or_uri) -> list[Track].
