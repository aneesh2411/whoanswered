"""Build the byte-stable reviewer archive for the public v1 pack."""

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from .fixtures import FixtureValidationError, load_pack


ROOT = Path(__file__).resolve().parent.parent
PACK = Path("fixtures/v1")
REFERENCE = Path("tests/expected/v1/scorecard.json")
REVIEWER_SOURCES = REFERENCE.parent / "reviewer"
REVIEWER_MODULES = (
    "__init__", "__main__", "baseline", "bundle", "comparison", "fixtures",
    "policy", "replay", "scorecard", "selection", "veto",
)
FROZEN_MODULES = {"__init__", "__main__", "bundle", "replay"}
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ZIP_FILE_MODE = 0o100644


def bundle(pack: Path, out: Path) -> None:
    """Package the public v1 inputs and its pinned reference scorecard."""
    load_pack(pack)
    root = ROOT
    if not (root / REVIEWER_SOURCES).is_dir():
        root = next(
            (parent for parent in (Path.cwd(), *pack.resolve().parents) if (parent / REVIEWER_SOURCES).is_dir()),
            ROOT,
        )
    manifest = (pack / "manifest.json").read_bytes()
    if manifest != (root / PACK / "manifest.json").read_bytes():
        raise FixtureValidationError("BUNDLE_PACK_MISMATCH", str(pack))

    files = {
        "README.md": root / REVIEWER_SOURCES / "README.md",
        "LICENSE": root / "LICENSE",
        "fixtures/LICENSE.md": root / "fixtures" / "LICENSE.md",
        "reference/scorecard.json": root / REFERENCE,
    }
    # The reviewer ZIP embeds source. Use the frozen copies of modules changed
    # by the library release so the archive remains identical.
    for name in REVIEWER_MODULES:
        source_dir = REVIEWER_SOURCES if name in FROZEN_MODULES else Path("whoanswered")
        files[f"whoanswered/{name}.py"] = root / source_dir / f"{name}.py"

    pack_files = {"manifest.json", "README.md"}
    for case in json.loads(manifest)["cases"]:
        pack_files.update(payload["path"] for payload in case["payloads"].values())
    files.update((f"{PACK.as_posix()}/{name}", pack / name) for name in pack_files)

    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = NamedTemporaryFile(dir=out.parent, delete=False)
    temporary_path = Path(temporary.name)
    try:
        with temporary, ZipFile(temporary, "w") as archive:
            for name, source in sorted(files.items()):
                entry = ZipInfo(name, date_time=ZIP_TIMESTAMP)
                entry.create_system = 3
                entry.external_attr = ZIP_FILE_MODE << 16
                archive.writestr(entry, source.read_bytes(), compress_type=ZIP_STORED)
        temporary_path.replace(out)
    finally:
        temporary_path.unlink(missing_ok=True)
