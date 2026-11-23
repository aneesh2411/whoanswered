"""Command-line entry point for WhoAnswered."""

import argparse
from pathlib import Path

from .replay import replay


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m whoanswered")
    commands = parser.add_subparsers(required=True)
    replay_parser = commands.add_parser("replay", help="Replay a local fixture pack")
    replay_parser.add_argument("--pack", required=True, type=Path, help="Fixture directory")
    replay_parser.add_argument("--out", required=True, type=Path, help="Output directory")
    args = parser.parse_args()

    try:
        replay(args.pack, args.out)
    except OSError as error:
        parser.exit(1, f"whoanswered: replay failed: {error.strerror}\n")


if __name__ == "__main__":
    main()
