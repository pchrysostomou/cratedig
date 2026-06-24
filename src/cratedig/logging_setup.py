# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""Logging configuration (Rich-backed)."""

from __future__ import annotations

import logging

from rich.logging import RichHandler


def setup_logging(verbose: bool = False) -> None:
    """Configure Rich logging: WARNING by default, INFO when ``verbose``."""
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=False, show_path=False)],
        force=True,
    )
