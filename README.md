# cratedig

> **Spotify-to-Audio CLI.** Give it a Spotify track, album, or playlist URL and it
> fetches the metadata, finds the matching audio on YouTube, transcodes to MP3, and
> embeds ID3 tags, cover art, and lyrics.

**Status:** early scaffold (Phase 0). Not yet functional — see [`DESIGN.md`](DESIGN.md)
for the full architecture and the phased build plan.

## Requirements

- **Python 3.10+**
- **[FFmpeg](https://ffmpeg.org/)** on your `PATH` (an external binary, *not* a pip
  package). On Windows: `winget install Gyan.FFmpeg`.

## Install (development)

```bash
git clone https://github.com/<you>/cratedig.git
cd cratedig
pip install -e .
```

## Usage

```bash
crate --help
crate --version
crate download <spotify-url>   # not implemented yet
```

You'll need Spotify API credentials (Client ID + Secret). Copy `.env.example` to `.env`
and fill them in — see [`DESIGN.md`](DESIGN.md) §9.

## Legal

This tool reads only public Spotify **metadata** (no DRM is circumvented) and downloads
matching audio from YouTube via `yt-dlp`. The download step sits in a legal gray area that
depends on your jurisdiction and the Spotify/YouTube Terms of Service. Use it only for
content you have the right to use, and respect your local copyright law.

## License

`cratedig` is free software, licensed under the **GNU General Public License v3.0 or later
(GPL-3.0-or-later)**. See [`LICENSE`](LICENSE) for the full text.
