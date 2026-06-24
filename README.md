# cratedig

> **Music-to-Audio CLI.** Give it a search query — or a MusicBrainz release/recording URL or
> MBID — and it fetches the metadata, finds the matching audio on YouTube, transcodes to MP3,
> and embeds ID3 tags, cover art, and lyrics.

**Status:** in development. The core pipeline works end-to-end (single-threaded); concurrent
downloads and packaged builds land in later phases. This README is a stub — full install/usage
docs come in Phase 7. See [`DESIGN.md`](DESIGN.md) for the architecture and phased build plan.

## Requirements

- **Python 3.10+**
- **[FFmpeg](https://ffmpeg.org/)** on your `PATH` (an external binary, *not* a pip package).
  On Windows: `winget install Gyan.FFmpeg`.
- **No API keys** — metadata comes from [MusicBrainz](https://musicbrainz.org/), which is free
  and keyless.

## Install (development)

```bash
git clone https://github.com/pchrysostomou/cratedig.git
cd cratedig
pip install -e .
```

## Usage

```bash
crate --help
crate --version
crate download "daft punk - get lucky"                       # free-text search
crate download "https://musicbrainz.org/recording/<mbid>"    # a recording (single track)
crate download "https://musicbrainz.org/release/<mbid>"      # a release (album)
```

No credentials or configuration are required. Optional defaults (output directory, audio
format, bitrate, workers, browser cookies) can be set via CLI flags or a local `.env` — see
[`DESIGN.md`](DESIGN.md) §9.

## Legal

This tool reads only public **MusicBrainz** metadata (no DRM is circumvented) and downloads
matching audio from YouTube via `yt-dlp`. The download step sits in a legal gray area that
depends on your jurisdiction and the YouTube Terms of Service. Use it only for content you have
the right to use, and respect your local copyright law.

## License

`cratedig` is free software, licensed under the **GNU General Public License v3.0 or later
(GPL-3.0-or-later)**. See [`LICENSE`](LICENSE) for the full text.
