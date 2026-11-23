"""Command-line entry point for WhoAnswered."""

import argparse
from pathlib import Path

from .fixtures import FixtureValidationError, load_pack
from .replay import replay


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m whoanswered")
    commands = parser.add_subparsers(dest="command", required=True)
    validate_parser = commands.add_parser("validate", help="Validate a local fixture pack")
    validate_parser.add_argument("--pack", required=True, type=Path, help="Fixture directory")
    replay_parser = commands.add_parser("replay", help="Replay a local fixture pack")
    replay_parser.add_argument("--pack", required=True, type=Path, help="Fixture directory")
    replay_parser.add_argument("--out", required=True, type=Path, help="Output directory")
    args = parser.parse_args()

    try:
        if args.command == "validate":
            load_pack(args.pack)
        else:
            replay(args.pack, args.out)
    except FixtureValidationError as error:
        parser.exit(1, f"whoanswered: {error}\n")
    except OSError as error:
        parser.exit(1, f"whoanswered: {args.command} failed: {error.strerror}\n")


if __name__ == "__main__":
    main()
