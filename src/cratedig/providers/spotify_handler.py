# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""Spotify metadata provider (Phase 1).

Client-Credentials auth (read-only catalog). Detects track/album/playlist from a
URL or URI, fetches metadata (fully paginated), and returns a normalized
``list[Track]`` matching ``models.py``. See DESIGN.md §5, §6, §7, §8.
"""

from __future__ import annotations

import re

import requests
from spotipy import Spotify, SpotifyException
from spotipy.oauth2 import SpotifyClientCredentials

from cratedig.exceptions import InvalidUrlError, SpotifyApiError
from cratedig.models import Track

# spotify:track:<id> / spotify:album:<id> / spotify:playlist:<id>
_URI_RE = re.compile(r"^spotify:(track|album|playlist):([A-Za-z0-9]+)$")

# https://open.spotify.com/[intl-xx/]{track,album,playlist}/<id>[/][?si=...]
# End-anchored so trailing path/punctuation is rejected (not silently truncated),
# while still allowing an optional trailing slash and a ?query string.
_URL_RE = re.compile(
    r"^https?://open\.spotify\.com/(?:intl-[a-z]{2}/)?"
    r"(track|album|playlist)/([A-Za-z0-9]+)/?(?:\?.*)?$"
)

_PLAYLIST_PAGE = 100  # Spotify max page size for playlist items (album tracks page at 50)


class SpotifyHandler:
    """Fetches clean track metadata from the Spotify Web API."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        auth_manager = SpotifyClientCredentials(
            client_id=client_id,
            client_secret=client_secret,
        )
        # Spotipy's built-in urllib3 Retry handles HTTP 429 and honors the
        # Retry-After header automatically (429 is in the default status list),
        # so we never hand-roll backoff. See DESIGN.md §7.
        self._sp = Spotify(
            auth_manager=auth_manager,
            requests_timeout=10,
            retries=10,
            status_retries=10,
            backoff_factor=0.3,
        )

    # -- public API ---------------------------------------------------------

    def fetch(self, url_or_uri: str) -> list[Track]:
        """Return the tracks for a Spotify track/album/playlist URL or URI."""
        kind, spotify_id = self._parse_input(url_or_uri)
        try:
            if kind == "track":
                return self._fetch_track(spotify_id)
            if kind == "album":
                return self._fetch_album(spotify_id)
            return self._fetch_playlist(spotify_id)
        except (SpotifyException, requests.RequestException) as exc:
            raise SpotifyApiError(f"Spotify API request failed: {exc}") from exc

    # -- input parsing ------------------------------------------------------

    @staticmethod
    def _parse_input(url_or_uri: str) -> tuple[str, str]:
        """Detect ``(kind, id)`` from a URL or URI; raise InvalidUrlError otherwise."""
        if isinstance(url_or_uri, str):
            match = _URI_RE.match(url_or_uri.strip()) or _URL_RE.match(url_or_uri.strip())
            if match:
                return match.group(1), match.group(2)
        raise InvalidUrlError(
            f"Unrecognized Spotify track/album/playlist URL or URI: {url_or_uri!r}"
        )

    # -- per-type fetchers --------------------------------------------------

    def _fetch_track(self, track_id: str) -> list[Track]:
        track = self._sp.track(track_id)
        return [self._map_track(track, track["album"])]

    def _fetch_album(self, album_id: str) -> list[Track]:
        album = self._sp.album(album_id)
        page = album["tracks"]
        items = list(page["items"])
        while page.get("next"):
            page = self._sp.next(page)
            items.extend(page["items"])
        # Album-tracks payloads are *simplified* track objects (no per-track album
        # block, images, or external_ids), so source album name + cover art from
        # the album object fetched once and apply it to every track.
        return [self._map_track(item, album) for item in items]

    def _fetch_playlist(self, playlist_id: str) -> list[Track]:
        page = self._sp.playlist_items(
            playlist_id,
            limit=_PLAYLIST_PAGE,
            additional_types=("track",),
        )
        items = list(page["items"])
        while page.get("next"):
            page = self._sp.next(page)
            items.extend(page["items"])
        tracks: list[Track] = []
        for item in items:
            track = item.get("track")
            if not track or not track.get("id"):
                continue  # removed / unavailable / local entries carry no usable track
            tracks.append(self._map_track(track, track["album"]))
        return tracks

    # -- mapping ------------------------------------------------------------

    @staticmethod
    def _map_track(track: dict, album: dict) -> Track:
        images = album.get("images") or []
        release_date = album.get("release_date") or ""
        return Track(
            title=track["name"],
            artists=[artist["name"] for artist in track.get("artists", [])],
            album=album.get("name", ""),
            isrc=track.get("external_ids", {}).get("isrc"),
            duration_ms=track["duration_ms"],
            track_number=track["track_number"],
            disc_number=track["disc_number"],
            release_year=release_date[:4] if release_date else None,
            cover_art_url=images[0]["url"] if images else None,
            spotify_id=track["id"],
            lyrics=None,
        )
