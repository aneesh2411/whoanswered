"""Command-line entry point for WhoAnswered."""

import argparse
from pathlib import Path

from ._plugins import DetectorSpecError, load_detector_factory
from .bundle import bundle
from .comparison import DETECTOR_FACTORIES, compare
from .fixtures import FixtureValidationError, load_pack, validate_split_boundary
from .replay import replay
from .selection import tune


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
    compare_parser.add_argument("--pack", required=True, type=Path, help="Revision 2 or 3 evaluation fixture directory")
    compare_parser.add_argument(
        "--policy-pack", type=Path, help="Fixture directory for the separate scripted policy checks",
    )
    compare_parser.add_argument("--out", required=True, type=Path, help="Output directory")
    # Keep the published help text byte-stable. Plugin syntax is in docs/api.md.
    compare_parser.add_argument("--detector", help=argparse.SUPPRESS)
    tune_parser = commands.add_parser("tune", help="Select the operating point on development cases")
    tune_parser.add_argument("--pack", required=True, type=Path, help="Revision 2 dev-only fixture directory")
    tune_parser.add_argument("--out", required=True, type=Path, help="Output directory")
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
            replay(args.pack, args.out, detector_factory=load_detector_factory(args.detector))
        elif args.command == "compare":
            factories = None if args.detector is None else {
                "v1": DETECTOR_FACTORIES["v1"],
                args.detector: load_detector_factory(args.detector),
            }
            compare(args.pack, args.out, policy_pack=args.policy_pack, detector_factories=factories)
        elif args.command == "tune":
            tune(args.pack, args.out)
        else:
            bundle(args.pack, args.out)
    except DetectorSpecError as error:
        parser.exit(2, f"whoanswered: {error}\n")
    except FixtureValidationError as error:
        parser.exit(1, f"whoanswered: {error}\n")
    except OSError as error:
        parser.exit(1, f"whoanswered: {args.command} failed: {error.strerror}\n")


if __name__ == "__main__":
    main()
