# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""YouTube audio downloader (Phase 3).

Stub — implemented in a later phase. Wraps yt-dlp (``bestaudio`` → FFmpeg).
Idempotent (skip if the target file exists), supports ``--cookies-from-browser``
and per-request jitter. See DESIGN.md §6.
"""

# TODO: implement YouTubeDownloader(...).download(video_url, track) -> str.
