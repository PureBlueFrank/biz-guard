"""Module entrypoint matching ``python -m bizguard.impact analyze``."""

from __future__ import annotations
from bizguard.cli import main

if __name__ == "__main__":
    import sys

    raise SystemExit(main(["impact", *sys.argv[1:]]))
