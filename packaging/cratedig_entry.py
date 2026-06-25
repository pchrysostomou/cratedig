# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""PyInstaller entry point for the standalone Windows build.

Runs the same Typer app that backs the ``crate`` console command; all CLI and pipeline logic
lives in ``cratedig.cli`` (unchanged). This launcher exists only because PyInstaller needs a
script file as its analysis entry point, not a ``module:attr`` console-script reference.
"""

from cratedig.cli import app

if __name__ == "__main__":
    app()
