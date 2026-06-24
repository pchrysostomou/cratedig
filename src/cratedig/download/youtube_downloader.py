# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""YouTube audio downloader (Phase 3) — bestaudio -> FFmpeg -> mp3.

Given a YouTube watch URL and a Track, download the best audio, transcode to the
configured format via FFmpeg, and return the final file path. Idempotent: if the
deterministic target file already exists it is returned without re-downloading.
See DESIGN.md §6, §8, §10.
"""

from __future__ import annotations

import re
from pathlib import Path
from shutil import which

from yt_dlp import YoutubeDL

from cratedig.exceptions import DownloadError
from cratedig.models import Track

_FFMPEG_MISSING_MSG = "FFmpeg is required and was not found — install it (see README)."

# Characters illegal in Windows filenames, plus C0/C1 control chars and DEL.
_ILLEGAL_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f-\x9f]')
_MAX_BASENAME = 180

# Codecs whose produced file extension differs from the codec name (yt-dlp ACODECS).
_CODEC_TO_EXT = {"aac": "m4a", "alac": "m4a", "vorbis": "ogg"}


def _output_ext(audio_format: str) -> str:
    """The actual file extension FFmpegExtractAudio produces for a codec."""
    return _CODEC_TO_EXT.get(audio_format, audio_format)


def _safe_filename(track: Track, ext: str) -> str:
    """Deterministic, filesystem-safe ``"<artist> - <title>.<ext>"`` from the Track.

    Derived from the *Track* (never the YouTube title) so runs are reproducible
    and idempotent. Strips Windows-illegal characters and trailing dots/spaces.
    """
    base = f"{track.primary_artist} - {track.title}"
    base = _ILLEGAL_FILENAME_RE.sub(" ", base)
    base = re.sub(r"\s+", " ", base).strip().rstrip(". ")
    if not re.search(r"[A-Za-z0-9]", base):  # nothing usable survived sanitizing
        base = track.spotify_id or "track"
    base = base[:_MAX_BASENAME].strip().rstrip(". ")
    return f"{base}.{ext}"


class YouTubeDownloader:
    """Downloads + transcodes a single YouTube video's audio to a known path."""

    def __init__(
        self,
        output_dir,
        audio_format: str = "mp3",
        bitrate: str = "192",
        cookies_from_browser: str | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.audio_format = audio_format
        self.bitrate = bitrate
        self.cookies_from_browser = cookies_from_browser

    def download(self, video_url: str, track: Track) -> str:
        """Download + transcode ``video_url`` for ``track``; return the file path."""
        target = self.output_dir / _safe_filename(track, _output_ext(self.audio_format))

        # Idempotency: an existing file is returned untouched (no yt-dlp, no FFmpeg).
        if target.exists():
            return str(target)

        # Fail fast + clearly if FFmpeg is absent (the #1 gotcha, DESIGN.md §10),
        # rather than letting yt-dlp surface a version-dependent stack trace.
        if which("ffmpeg") is None:
            raise DownloadError(_FFMPEG_MISSING_MSG)

        self.output_dir.mkdir(parents=True, exist_ok=True)
        opts = self._build_opts(target)

        try:
            with YoutubeDL(opts) as ydl:
                ydl.download([video_url])
        except Exception as exc:  # wrap ANY yt-dlp/FFmpeg/IO failure; never let it escape
            text = str(exc).lower()
            # Fallback for the edge case where ffmpeg/ffprobe is reported missing despite
            # the proactive which() guard. Require a not-found phrasing so genuine transcode
            # errors (e.g. "ffmpeg exited with code 1") are not mislabeled as a missing install.
            binary_missing = ("ffmpeg" in text or "ffprobe" in text) and (
                "not found" in text or "not installed" in text or "no such file" in text
            )
            if binary_missing:
                raise DownloadError(_FFMPEG_MISSING_MSG) from exc
            raise DownloadError(f"Failed to download {track.search_query!r}: {exc}") from exc

        if not target.exists():
            raise DownloadError(
                f"Download reported success but produced no output for "
                f"{track.search_query!r} (expected {target})."
            )
        return str(target)

    def _build_opts(self, target: Path) -> dict:
        # outtmpl has no hardcoded extension; yt-dlp fills %(ext)s and the
        # FFmpegExtractAudio post-processor rewrites it to the target extension.
        base = str(target.with_suffix(""))
        opts: dict = {
            "format": "bestaudio/best",
            "outtmpl": f"{base}.%(ext)s",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": self.audio_format,
                    "preferredquality": self.bitrate,
                }
            ],
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        if self.cookies_from_browser:
            opts["cookiesfrombrowser"] = (self.cookies_from_browser,)
        return opts
