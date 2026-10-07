"""Verify the extension ZIP contains exactly the reviewed adapter source."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

from build_extension import PACKAGE_ID, ROOT, extension_files, read_manifest


def verify_extension(archive_path: Path, root: Path = ROOT) -> None:
    """Reject missing, changed, duplicate or unexpected archive entries."""
    expected = extension_files(root)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Extension archive contains duplicate entries")
        if set(names) != set(expected):
            raise ValueError("Extension archive does not match the selected source files")
        for name, content in expected.items():
            if archive.read(name) != content:
                raise ValueError(f"Extension archive content differs from source: {name}")
            entry = archive.getinfo(name)
            if entry.create_system == 3 and (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"Extension archive contains a symlink: {name}")
        if archive.testzip() is not None:
            raise ValueError("Extension archive failed its CRC check")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, nargs="?", help="Archive to verify")
    args = parser.parse_args()
    manifest = read_manifest(ROOT)
    archive_path = args.archive or ROOT / "dist" / f"{PACKAGE_ID}-{manifest['version']}.zip"
    verify_extension(archive_path)
    print("Extension root layout and source contents verified; native support is not certified")


if __name__ == "__main__":
    main()
