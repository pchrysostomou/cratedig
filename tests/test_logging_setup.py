"""Tests for Rich logging setup."""

from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

from cratedig.logging_setup import setup_logging


def _rich_handlers():
    return [h for h in logging.getLogger().handlers if isinstance(h, RichHandler)]


def test_setup_logging_routes_through_given_console():
    # A supplied console is used by the RichHandler so logs serialize with a live Progress bar.
    console = Console()
    setup_logging(verbose=False, console=console)
    handlers = _rich_handlers()
    assert handlers and handlers[0].console is console
    assert logging.getLogger().level == logging.WARNING


def test_setup_logging_verbose_sets_info_level():
    setup_logging(verbose=True, console=Console())
    assert logging.getLogger().level == logging.INFO


def test_setup_logging_defaults_to_own_console():
    # No console supplied -> RichHandler falls back to its default (does not raise).
    setup_logging(verbose=False)
    handlers = _rich_handlers()
    assert handlers and handlers[0].console is not None
