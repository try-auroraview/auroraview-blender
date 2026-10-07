"""Build a reproducible, dependency-free Blender extension from adapter source."""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 tooling only; never imported by the add-on.
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "blender_manifest.toml"
PACKAGE_ID = "auroraview_blender"


def read_manifest(root: Path) -> dict:
    """Check our packaging contract; Blender performs full schema validation."""
    with (root / MANIFEST_NAME).open("rb") as source:
        manifest = tomllib.load(source)
    expected = {
        "schema_version": "1.0.0",
        "id": PACKAGE_ID,
        "type": "add-on",
        "blender_version_min": "4.2.0",
        "license": ["SPDX:MIT"],
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"Unexpected extension manifest {key}")
    for key in ("name", "maintainer", "tagline"):
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            raise ValueError(f"Missing extension manifest {key}")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", manifest.get("version", "")):
        raise ValueError("Extension version must contain three numeric components")
    if len(manifest["tagline"]) > 64 or manifest["tagline"][-1] in ".!?":
        raise ValueError("Extension tagline must be at most 64 characters without punctuation")
    if "wheels" in manifest:
        raise ValueError("This extension must not bundle dependencies")
    return manifest


def extension_files(root: Path) -> dict[str, bytes]:
    """Select only adapter Python source, manifest and license for the archive."""
    read_manifest(root)
    package = root / "src" / PACKAGE_ID
    if package.is_symlink() or not package.is_dir():
        raise ValueError("Adapter source must be a real directory")
    files = {}
    for source in sorted(package.rglob("*")):
        relative = source.relative_to(package)
        if "__pycache__" in relative.parts:
            continue
        if source.is_symlink():
            raise ValueError(f"Extension source must not contain symlinks: {relative}")
        if source.is_file() and source.suffix == ".py":
            if not all(part.isidentifier() for part in relative.with_suffix("").parts):
                raise ValueError(f"Invalid Python module path: {relative}")
            files[relative.as_posix()] = source.read_bytes()
    if "__init__.py" not in files:
        raise ValueError("Extension requires an add-on entry point")
    for name in (MANIFEST_NAME, "LICENSE"):
        source = root / name
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Extension requires a regular {name} file")
        files[name] = source.read_bytes()
    return dict(sorted(files.items()))


def build_extension(root: Path = ROOT, output: Path | None = None) -> Path:
    """Write a root-layout ZIP with fixed timestamps, permissions and ordering."""
    manifest = read_manifest(root)
    files = extension_files(root)
    if output is None:
        output = root / "dist" / f"{PACKAGE_ID}-{manifest['version']}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    # Stored entries avoid differences between compressor versions and keep the
    # small Python-only adapter reproducible across the supported toolchain.
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in files.items():
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, content)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Destination ZIP (defaults to dist/)")
    args = parser.parse_args()
    print(f"Built {build_extension(output=args.output).name}")


if __name__ == "__main__":
    main()
