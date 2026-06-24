# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""Command-line interface for cratedig (Typer app).

Phase 0 scaffold: exposes ``--version`` and a placeholder ``download`` command so
that ``crate --help`` works. The real pipeline is wired up in later phases.
"""

import typer

from cratedig import __version__

app = typer.Typer(
    name="crate",
    help="Spotify-to-Audio CLI — fetch metadata, find audio on YouTube, tag + embed lyrics.",
    add_completion=False,
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"cratedig {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the cratedig version and exit.",
        is_eager=True,
        callback=_version_callback,
    ),
) -> None:
    """cratedig — download audio for Spotify tracks, albums, and playlists."""


@app.command()
def download(
    url: str = typer.Argument(..., help="Spotify track / album / playlist URL or URI."),
) -> None:
    """Download audio for a Spotify URL (not implemented yet)."""
    typer.echo("not implemented yet")
