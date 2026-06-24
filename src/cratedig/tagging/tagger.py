# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""Audio tagger (Phase 4) — ID3 tags + cover art + lyrics.

Writes metadata to the output file: ID3 frames for ``.mp3``, MP4 atoms for
``.m4a``/``.mp4``, portable tags otherwise. Cover art is best-effort (a failed
image download never aborts tagging). A genuine write failure raises
``TaggingError``; the audio file is kept (DESIGN.md §6, §7).
"""

from __future__ import annotations

from pathlib import Path

import mutagen
import requests
from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TDRC,
    TIT2,
    TPE1,
    TPOS,
    TRCK,
    TSRC,
    USLT,
    ID3NoHeaderError,
)
from mutagen.mp4 import MP4, MP4Cover

from cratedig.exceptions import TaggingError
from cratedig.models import Track

_USER_AGENT = "cratedig/0.1.0 (+https://github.com/pchrysostomou/cratedig)"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class Tagger:
    """Writes ID3/MP4 tags, cover art, and lyrics to a downloaded audio file."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def tag(self, file_path: str, track: Track) -> None:
        ext = Path(file_path).suffix.lower()
        try:
            if ext == ".mp3":
                self._tag_mp3(file_path, track)
            elif ext in (".m4a", ".mp4"):
                self._tag_mp4(file_path, track)
            else:
                self._tag_other(file_path, track)
        except (mutagen.MutagenError, OSError) as exc:
            # Genuine write failure (corrupt/unreadable file, mutagen error). The audio
            # file is kept. Programming errors are NOT relabeled here — they surface.
            raise TaggingError(f"Failed to tag {file_path!r}: {exc}") from exc

    # -- mp3 (ID3) ----------------------------------------------------------

    def _tag_mp3(self, file_path: str, track: Track) -> None:
        try:
            tags = ID3(file_path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.setall("TIT2", [TIT2(encoding=3, text=track.title)])
        tags.setall("TPE1", [TPE1(encoding=3, text=list(track.artists))])
        tags.setall("TALB", [TALB(encoding=3, text=track.album)])
        tags.setall("TRCK", [TRCK(encoding=3, text=str(track.track_number))])
        tags.setall("TPOS", [TPOS(encoding=3, text=str(track.disc_number))])
        if track.release_year:
            tags.setall("TDRC", [TDRC(encoding=3, text=track.release_year)])
        if track.isrc:
            tags.setall("TSRC", [TSRC(encoding=3, text=track.isrc)])
        cover = self._fetch_cover(track)
        if cover is not None:
            mime, data = cover
            tags.setall("APIC", [APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data)])
        if track.lyrics:
            tags.setall("USLT", [USLT(encoding=3, lang="eng", desc="", text=track.lyrics)])
        tags.save(file_path)

    # -- m4a / mp4 (atoms) --------------------------------------------------

    def _tag_mp4(self, file_path: str, track: Track) -> None:
        audio = MP4(file_path)
        audio["\xa9nam"] = [track.title]
        audio["\xa9ART"] = list(track.artists)
        audio["\xa9alb"] = [track.album]
        audio["trkn"] = [(track.track_number, 0)]
        audio["disk"] = [(track.disc_number, 0)]
        if track.release_year:
            audio["\xa9day"] = [track.release_year]
        cover = self._fetch_cover(track)
        if cover is not None:
            mime, data = cover
            fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
            audio["covr"] = [MP4Cover(data, imageformat=fmt)]
        if track.lyrics:
            audio["\xa9lyr"] = [track.lyrics]
        audio.save()

    # -- other formats (best-effort) ---------------------------------------

    def _tag_other(self, file_path: str, track: Track) -> None:
        # Unknown/unsupported formats are strictly best-effort: never crash.
        try:
            audio = mutagen.File(file_path, easy=True)
            if audio is None:
                return
            audio["title"] = track.title
            audio["artist"] = list(track.artists)
            audio["album"] = track.album
            audio["tracknumber"] = str(track.track_number)
            if track.release_year:
                audio["date"] = track.release_year
            audio.save()
        except Exception:  # best-effort: an unknown format must never raise
            return

    # -- cover art (best-effort) -------------------------------------------

    def _fetch_cover(self, track: Track) -> tuple[str, bytes] | None:
        if not track.cover_art_url:
            return None
        try:
            resp = requests.get(
                track.cover_art_url,
                headers={"User-Agent": _USER_AGENT},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.content
            if not data:
                return None
            mime = "image/png" if data.startswith(_PNG_MAGIC) else "image/jpeg"
            return mime, data
        except Exception:  # best-effort: a failed image must never abort tagging
            return None
