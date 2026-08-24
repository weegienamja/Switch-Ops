"""PyInstaller entry point that preserves the ``app`` package context."""

import sys

from app.cli import main as cli_main
from app.main import main as server_main


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in {
        "analyze",
        "paths",
        "failures",
        "capabilities",
        "performance",
    }:
        raise SystemExit(cli_main(sys.argv[1:]))
    server_main()
