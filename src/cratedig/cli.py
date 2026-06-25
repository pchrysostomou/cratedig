# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""Command-line interface for cratedig (Typer + Rich). See DESIGN.md §6.

Note: this module deliberately does NOT use ``from __future__ import annotations``
so Typer introspects the real option types at runtime.
"""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from yt_dlp import YoutubeDL

from cratedig import __version__
from cratedig.config import get_settings
from cratedig.core.orchestrator import Orchestrator
from cratedig.download.matcher import rank_candidates
from cratedig.download.youtube_downloader import YouTubeDownloader
from cratedig.exceptions import ProviderError
from cratedig.logging_setup import setup_logging
from cratedig.lyrics.lyrics_fetcher import fetch_lyrics
from cratedig.models import DownloadResult, ResultStatus
from cratedig.providers.musicbrainz_handler import MusicBrainzHandler
from cratedig.tagging.tagger import Tagger

app = typer.Typer(
    name="crate",
    help="Music-to-Audio CLI — MusicBrainz metadata, audio from YouTube, tagged + lyrics.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()

_STATUS_GLYPH = {
    ResultStatus.SUCCESS: "[green]✓[/green]",
    ResultStatus.SKIPPED: "[yellow]⤼[/yellow]",
    ResultStatus.NOT_FOUND: "[red]✗[/red]",
    ResultStatus.FAILED: "[red]✗[/red]",
}


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"cratedig {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Show the cratedig version and exit.",
            is_eager=True,
            callback=_version_callback,
        ),
    ] = False,
) -> None:
    """cratedig — download audio for MusicBrainz releases/recordings (or a search)."""


@app.command()
def download(
    query: Annotated[
        str,
        typer.Argument(help="MusicBrainz release/recording URL or MBID, or a search query."),
    ],
    audio_format: Annotated[
        str | None, typer.Option("--format", help="Audio format (default: mp3).")
    ] = None,
    bitrate: Annotated[
        str | None, typer.Option("--bitrate", help="Audio bitrate in kbps (default: 192).")
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", help="Output directory (default: ~/Music/cratedig).")
    ] = None,
    workers: Annotated[
        int | None, typer.Option("--workers", help="Download workers (default: 3).")
    ] = None,
    cookies_from_browser: Annotated[
        str | None,
        typer.Option(
            "--cookies-from-browser", help="Browser to read YouTube cookies from (anti-bot)."
        ),
    ] = None,
    no_lyrics: Annotated[bool, typer.Option("--no-lyrics", help="Skip lyrics fetching.")] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Enable verbose (INFO) logging.")
    ] = False,
) -> None:
    """Download audio for a MusicBrainz release/recording, or the best search match."""
    setup_logging(verbose)
    try:
        settings = get_settings(
            output_dir=output,
            audio_format=audio_format,
            bitrate=bitrate,
            max_workers=workers,
            cookies_from_browser=cookies_from_browser,
        )
        orchestrator = Orchestrator(
            handler=MusicBrainzHandler(),
            downloader=YouTubeDownloader(
                settings.output_dir,
                audio_format=settings.audio_format,
                bitrate=settings.bitrate,
                cookies_from_browser=settings.cookies_from_browser,
            ),
            ranker=rank_candidates,
            lyrics_fetcher=None if no_lyrics else fetch_lyrics,
            tagger=Tagger(),
            # ignoreerrors=True so an unavailable video in the ytsearch results becomes a None
            # entry (skipped by the matcher) instead of throwing in extract_info and killing the
            # whole track before any candidate exists. The downloader keeps ignoreerrors OFF so
            # real download failures still raise and the per-candidate fallback can advance.
            ydl=YoutubeDL({"quiet": True, "no_warnings": True, "ignoreerrors": True}),
            max_workers=settings.max_workers,
        )
        # A single shared Progress, advanced once per completed track. The orchestrator invokes
        # on_progress(done, total) on the main thread, so updates are sequential (Rich is also
        # thread-safe). total stays None until the first track completes (fetch runs first).
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Fetching & downloading", total=None)
            results = orchestrator.run(
                query,
                on_progress=lambda done, total: progress.update(task, completed=done, total=total),
            )
    except ProviderError as exc:  # InvalidUrlError / ProviderApiError (fatal)
        console.print(Panel(str(exc), title="cratedig error", border_style="red"))
        raise typer.Exit(code=1) from exc

    if not results:
        console.print(f"No tracks found for {query!r}.")
        return
    _print_summary(results)


def _print_summary(results: list[DownloadResult]) -> None:
    table = Table(title="cratedig results")
    table.add_column("Track", overflow="fold")
    table.add_column("Status", justify="center")
    table.add_column("Lyrics", justify="center")
    for result in results:
        glyph = _STATUS_GLYPH.get(result.status, "?")
        lyrics = "[green]✓[/green]" if result.lyrics_found else "[dim]✗[/dim]"
        table.add_row(result.track.search_query, glyph, lyrics)
    console.print(table)

    counts = {status: 0 for status in ResultStatus}
    for result in results:
        counts[result.status] += 1
    console.print(
        f"{counts[ResultStatus.SUCCESS]} downloaded, "
        f"{counts[ResultStatus.SKIPPED]} skipped, "
        f"{counts[ResultStatus.NOT_FOUND]} not found, "
        f"{counts[ResultStatus.FAILED]} failed"
    )
