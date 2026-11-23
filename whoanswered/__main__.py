"""Command-line entry point for WhoAnswered."""

import argparse
from pathlib import Path

from .baseline import AudioBaseline
from .bundle import bundle
from .comparison import compare
from .fixtures import FixtureValidationError, load_pack, validate_split_boundary
from .replay import replay


DETECTOR_FACTORIES = {"v1": AudioBaseline}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m whoanswered")
    commands = parser.add_subparsers(dest="command", required=True)
    validate_parser = commands.add_parser("validate", help="Validate local fixture packs")
    validate_parser.add_argument(
        "--pack", required=True, action="append", type=Path,
        help="Fixture directory (repeat to check a shared dev/eval boundary)",
    )
    replay_parser = commands.add_parser("replay", help="Replay a local fixture pack")
    replay_parser.add_argument("--pack", required=True, type=Path, help="Fixture directory")
    replay_parser.add_argument("--out", required=True, type=Path, help="Output directory")
    replay_parser.add_argument("--detector", default="v1", help="Detector name (default: v1)")
    compare_parser = commands.add_parser("compare", help="Compare bundled detectors on reserved evaluation cases")
    compare_parser.add_argument("--pack", required=True, type=Path, help="Revision 2 fixture directory")
    compare_parser.add_argument("--out", required=True, type=Path, help="Output directory")
    bundle_parser = commands.add_parser("bundle", help="Build the public v1 reviewer ZIP")
    bundle_parser.add_argument("--pack", required=True, type=Path, help="Public v1 fixture directory")
    bundle_parser.add_argument("--out", required=True, type=Path, help="Output ZIP file")
    args = parser.parse_args()

    try:
        if args.command == "validate":
            packs = [load_pack(pack) for pack in args.pack]
            if len(packs) > 1:
                validate_split_boundary(case for pack in packs for case in pack)
        elif args.command == "replay":
            factory = DETECTOR_FACTORIES.get(args.detector)
            if factory is None:
                available = ", ".join(DETECTOR_FACTORIES)
                parser.exit(2, f"whoanswered: unknown detector {args.detector!r} (choose from {available})\n")
            replay(args.pack, args.out, detector_factory=factory)
        elif args.command == "compare":
            compare(args.pack, args.out, detector_factories=DETECTOR_FACTORIES)
        else:
            bundle(args.pack, args.out)
    except FixtureValidationError as error:
        parser.exit(1, f"whoanswered: {error}\n")
    except OSError as error:
        parser.exit(1, f"whoanswered: {args.command} failed: {error.strerror}\n")


if __name__ == "__main__":
    main()
