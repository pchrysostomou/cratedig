# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""Logging configuration (Rich-backed)."""

from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler


def setup_logging(verbose: bool = False, console: Console | None = None) -> None:
    """Configure Rich logging: WARNING by default, INFO when ``verbose``.

    When ``console`` is supplied (the CLI's shared console), log records route through it so they
    serialize with a live Rich ``Progress`` display instead of corrupting it — worker threads now
    emit logs concurrently with the download progress bar (Phase 6). ``None`` keeps Rich's default
    console (current behavior for non-CLI callers).
    """
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=False, show_path=False)],
        force=True,
    )
