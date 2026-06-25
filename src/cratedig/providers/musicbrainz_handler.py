# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""MusicBrainz metadata provider (keyless, rate-limited).

No API key. Every request sends a descriptive User-Agent and ``fmt=json`` and is
spaced >= ``rate_limit_s`` apart (MusicBrainz returns HTTP 503 above ~1 req/s).
Accepts a MusicBrainz URL or bare MBID (``release`` -> album, ``recording`` ->
single track) or a free-text search query. Network/API/JSON failures are FATAL
and raise ``ProviderApiError`` (they propagate; this is not soft-fail).
See DESIGN.md §6.
"""

from __future__ import annotations

import logging
import re
import time

import requests
from rapidfuzz import fuzz

from cratedig.exceptions import InvalidUrlError, ProviderApiError
from cratedig.models import Track

logger = logging.getLogger(__name__)

MB_BASE = "https://musicbrainz.org/ws/2"
USER_AGENT = "cratedig/0.1.0 ( https://github.com/pchrysostomou/cratedig )"
CAA_BASE = "https://coverartarchive.org"
RATE_LIMIT_S = 1.0

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_UUID_RE = re.compile(rf"^{_UUID}$", re.IGNORECASE)
# [\w-] so hyphenated entities (e.g. release-group) are captured and then rejected, not
# mis-read as a search query.
_URL_RE = re.compile(rf"^https?://(?:beta\.)?musicbrainz\.org/([\w-]+)/({_UUID})", re.IGNORECASE)
_SEARCH_LIMIT = 10
_TIMEOUT = 15

# Search-relevance tuning.
_ARTIST_MATCH_THRESHOLD = 85  # rapidfuzz score below which a candidate is not the asked artist
_VARIANT_WORDS = ("remix", "live", "cover", "karaoke", "instrumental")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


class MusicBrainzHandler:
    """Fetches clean track metadata from MusicBrainz (keyless, rate-limited)."""

    def __init__(self, rate_limit_s: float = RATE_LIMIT_S) -> None:
        self.rate_limit_s = rate_limit_s
        self._last_request: float | None = None

    # -- public API ---------------------------------------------------------

    def fetch(self, query_or_url_or_mbid: str) -> list[Track]:
        """Return tracks for a MB URL/MBID, or the best match for a search query."""
        kind, value = self._classify(query_or_url_or_mbid)  # may raise InvalidUrlError
        try:
            if kind == "release":
                return self._fetch_release(value)
            if kind == "recording":
                return self._fetch_recording(value)
            if kind == "mbid":
                return self._fetch_by_mbid(value)
            return self._search(value)
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            # ValueError covers JSON decode errors; KeyError/TypeError cover malformed
            # (but valid-JSON) payloads missing required keys (e.g. an "id"). All fatal.
            raise ProviderApiError(f"MusicBrainz request failed: {exc}") from exc

    # -- input classification ----------------------------------------------

    @staticmethod
    def _classify(value: str) -> tuple[str, str]:
        text = value.strip()
        url_match = _URL_RE.match(text)
        if url_match:
            entity = url_match.group(1).lower()
            if entity in ("release", "recording"):
                return entity, url_match.group(2)
            raise InvalidUrlError(f"Unsupported MusicBrainz entity type: {entity!r}")
        if _UUID_RE.match(text):
            return "mbid", text  # bare MBID is type-ambiguous (recording-first, release fallback)
        return "search", text

    # -- rate-limited HTTP --------------------------------------------------

    def _get(self, path: str, params: dict) -> dict:
        if self._last_request is not None:
            wait = self.rate_limit_s - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
        self._last_request = time.monotonic()
        resp = requests.get(
            f"{MB_BASE}{path}",
            params={**params, "fmt": "json"},
            headers={"User-Agent": USER_AGENT},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    # -- entity fetchers ----------------------------------------------------

    def _fetch_by_mbid(self, mbid: str) -> list[Track]:
        # A bare MBID has no entity type: try recording (track-centric), then release on 404.
        try:
            return self._fetch_recording(mbid)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return self._fetch_release(mbid)
            raise

    def _fetch_recording(self, mbid: str) -> list[Track]:
        data = self._get(f"/recording/{mbid}", {"inc": "artist-credits+releases+isrcs"})
        releases = data.get("releases") or []
        release = releases[0] if releases else None
        return [self._map(data, release, track_number=1, disc_number=1)]

    def _fetch_release(self, mbid: str) -> list[Track]:
        data = self._get(f"/release/{mbid}", {"inc": "recordings+artist-credits+isrcs"})
        tracks: list[Track] = []
        for disc_number, medium in enumerate(data.get("media") or [], start=1):
            for track_pos, track_entry in enumerate(medium.get("tracks") or [], start=1):
                recording = track_entry.get("recording")
                if not recording:
                    continue
                track_number = track_entry.get("position") or track_pos
                tracks.append(
                    self._map(recording, data, track_number=track_number, disc_number=disc_number)
                )
        return tracks

    def _search(self, query: str) -> list[Track]:
        artist, title = self._split_query(query)
        data = self._get(
            "/recording", {"query": self._lucene(artist, title), "limit": _SEARCH_LIMIT}
        )
        recordings = data.get("recordings") or []
        if not recordings:
            return []

        # Artist gate (the core relevance fix): when the user named an artist, drop any
        # candidate whose credit doesn't match it, so we never pick a cover/remix/tribute.
        if artist:
            recordings = [
                r for r in recordings if self._artist_score(artist, r) >= _ARTIST_MATCH_THRESHOLD
            ]
            if not recordings:
                logger.warning(
                    "No MusicBrainz recording credited to %r for query %r", artist, query
                )
                return []

        best = max(recordings, key=lambda r: self._rank_key(artist, title, r))
        return self._fetch_recording(best["id"])

    # -- search ranking helpers --------------------------------------------

    @staticmethod
    def _split_query(query: str) -> tuple[str | None, str]:
        """Split ``"artist - title"`` on the FIRST ``" - "``; no dash -> (None, whole)."""
        left, sep, right = query.strip().partition(" - ")
        if sep:
            return left.strip(), right.strip()
        return None, query.strip()

    @staticmethod
    def _lucene(artist: str | None, title: str) -> str:
        """Structured Lucene query with escaped, quoted phrases."""

        def phrase(value: str) -> str:
            # Inside a quoted phrase only backslash and double-quote are special.
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

        if artist:
            return f"recording:{phrase(title)} AND artist:{phrase(artist)}"
        return f"recording:{phrase(title)}"

    @staticmethod
    def _norm(text: str) -> str:
        return _NON_ALNUM_RE.sub(" ", (text or "").lower()).strip()

    @classmethod
    def _artist_score(cls, artist: str, recording: dict) -> float:
        """Best fuzzy match between the requested artist and the recording's credits.

        Uses ``token_set_ratio`` (subset-tolerant) so the requested name still matches a
        fuller MB credit — e.g. "Beatles" vs "The Beatles", "Beyonce" vs "Beyonce Knowles",
        or an "X feat. Y" credit — while an unrelated cover/tribute artist still scores low.
        """
        wanted = cls._norm(artist)
        names = [
            credit["name"]
            for credit in recording.get("artist-credit") or []
            if isinstance(credit, dict) and credit.get("name")
        ]
        if not names:
            return 0.0
        return max(fuzz.token_set_ratio(wanted, cls._norm(name)) for name in names)

    @classmethod
    def _is_variant(cls, title: str, recording: dict) -> bool:
        """True if the candidate looks like a variant the user did not ask for."""
        cand = f" {cls._norm(recording.get('title', ''))} "
        wanted = f" {cls._norm(title)} "
        for word in _VARIANT_WORDS:
            token = f" {word} "
            if token in cand and token not in wanted:
                return True
        for release in recording.get("releases") or []:
            secondary = (release.get("release-group") or {}).get("secondary-types") or []
            for sec in secondary:
                low = str(sec).lower()
                # whole-word check vs the padded title (NOT a raw substring) so e.g. a Live
                # release of "Deliver" is not allowed just because "live" is inside "deliver".
                if low in ("remix", "live") and f" {low} " not in wanted:
                    return True
        return False

    @classmethod
    def _rank_key(cls, artist: str | None, title: str, recording: dict) -> tuple:
        # artist quality -> title similarity -> non-variant -> MB score -> id (stable tie-break).
        artist_score = cls._artist_score(artist, recording) if artist else 0.0
        title_sim = fuzz.token_sort_ratio(cls._norm(title), cls._norm(recording.get("title", "")))
        not_variant = 0 if cls._is_variant(title, recording) else 1
        return (
            artist_score,
            title_sim,
            not_variant,
            recording.get("score", 0),
            recording.get(
                "id", ""
            ),  # deterministic: same query -> same pick regardless of API order
        )

    # -- mapping ------------------------------------------------------------

    @staticmethod
    def _map(
        recording: dict, release: dict | None, *, track_number: int, disc_number: int
    ) -> Track:
        artists = [
            credit["name"]
            for credit in recording.get("artist-credit") or []
            if isinstance(credit, dict) and credit.get("name")
        ]
        isrcs = recording.get("isrcs") or []
        release = release or {}
        release_date = release.get("date") or ""
        release_mbid = release.get("id")
        return Track(
            title=recording.get("title", ""),
            artists=artists,
            album=release.get("title", ""),
            isrc=isrcs[0] if isrcs else None,
            duration_ms=recording.get("length") or 0,  # MB length is already in milliseconds
            track_number=track_number,
            disc_number=disc_number,
            release_year=release_date[:4] if release_date else None,
            cover_art_url=f"{CAA_BASE}/release/{release_mbid}/front-500" if release_mbid else None,
            source_id=recording["id"],
            lyrics=None,
        )
