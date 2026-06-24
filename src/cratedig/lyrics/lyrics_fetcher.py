# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""Lyrics fetcher (Phase 4) — LRCLIB lookup, soft-fail.

Hits LRCLIB directly with ``requests`` (no wrapper lib). ``fetch_lyrics`` NEVER
raises: any network/timeout/HTTP/JSON/field error returns ``None`` so a missing
lyric can never crash the pipeline (CLAUDE.md / DESIGN.md §7).
"""

from __future__ import annotations

import re

import requests

from cratedig.models import Track

LRCLIB_BASE = "https://lrclib.net/api"
USER_AGENT = "cratedig/0.1.0 (+https://github.com/pchrysostomou/cratedig)"

# Leading LRC timestamp tag(s) on a line, e.g. "[00:12.34]" or "[01:02]".
_TIMESTAMP_RE = re.compile(r"^(?:\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]\s*)+")
# A pure metadata line, e.g. "[ar: Artist]" / "[ti: Title]" / "[length: 03:20]".
_METADATA_RE = re.compile(r"^\[[a-zA-Z]+:[^\]]*\]\s*$")


def _strip_lrc(text: str) -> str:
    """Strip leading ``[mm:ss.xx]`` timestamps and drop metadata/empty lines."""
    out: list[str] = []
    for raw in text.splitlines():
        if _METADATA_RE.match(raw.strip()):
            continue
        line = _TIMESTAMP_RE.sub("", raw).strip()
        if line:
            out.append(line)
    return "\n".join(out)


def _record_to_lyrics(record: dict) -> str | None:
    """Plain lyrics from an LRCLIB record (plain preferred, else stripped synced)."""
    if not isinstance(record, dict) or record.get("instrumental"):
        return None
    plain = record.get("plainLyrics")
    if plain and plain.strip():
        return plain
    synced = record.get("syncedLyrics")
    if synced and synced.strip():
        return _strip_lrc(synced) or None  # stripped-to-empty counts as a miss
    return None


def _search_fallback(track: Track, headers: dict, timeout: float) -> str | None:
    resp = requests.get(
        f"{LRCLIB_BASE}/search",
        params={"track_name": track.title, "artist_name": track.primary_artist},
        headers=headers,
        timeout=timeout,
    )
    if resp.status_code != 200:
        return None
    candidates = resp.json()
    if not candidates:
        return None
    target_s = track.duration_ms / 1000
    best = min(candidates, key=lambda c: abs((c.get("duration") or 0) - target_s))
    return _record_to_lyrics(best)


def _fetch(track: Track, timeout: float) -> str | None:
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(
        f"{LRCLIB_BASE}/get",
        params={
            "track_name": track.title,
            "artist_name": track.primary_artist,
            "album_name": track.album,
            "duration": track.duration_ms // 1000,  # LRCLIB expects SECONDS, not ms
        },
        headers=headers,
        timeout=timeout,
    )
    if resp.status_code == 200:
        return _record_to_lyrics(resp.json())
    if resp.status_code == 404:
        return _search_fallback(track, headers, timeout)
    return None


def fetch_lyrics(track: Track, timeout: float = 10.0) -> str | None:
    """Return plain lyrics for ``track`` or ``None``. Never raises (soft-fail)."""
    try:
        return _fetch(track, timeout)
    except Exception:  # SOFT FAIL: lyrics must never crash the pipeline (DESIGN.md §7)
        return None
