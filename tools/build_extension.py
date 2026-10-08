"""Build a reproducible Blender extension with an optional local offscreen wheel."""

from __future__ import annotations

import argparse
import io
import re
import zipfile
from email.parser import BytesParser
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 tooling only; never imported by the add-on.
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "blender_manifest.toml"
PACKAGE_ID = "auroraview_blender"
OFFSCREEN_WHEEL = re.compile(r"auroraview_offscreen-([0-9][A-Za-z0-9_.!+]*)-py3-none-any\.whl")


def wheel_bytes(path: Path) -> bytes:
    """Accept only our standalone pure-Python client, keeping its wheel unmodified."""
    match = OFFSCREEN_WHEEL.fullmatch(path.name)
    if not match:
        raise ValueError("Offscreen wheel must be auroraview_offscreen-<version>-py3-none-any.whl")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Offscreen wheel must be a regular file no larger than 16 MiB")
    content = path.read_bytes()
    metadata_root = f"auroraview_offscreen-{match[1]}.dist-info"
    required = {f"{metadata_root}/{name}" for name in ("METADATA", "WHEEL", "RECORD")}
    required.add("auroraview_offscreen/__init__.py")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > 256 or sum(entry.file_size for entry in entries) > 32 * 1024 * 1024:
                raise ValueError("Offscreen wheel contents exceed the standalone client limit")
            if len(names) != len({name.casefold() for name in names}):
                raise ValueError("Offscreen wheel contains duplicate paths")
            for entry in entries:
                parts = entry.filename.split("/")
                if (
                    entry.is_dir()
                    or any(part in {"", ".", ".."} for part in parts)
                    or "\\" in entry.filename
                    or ":" in entry.filename
                    or parts[0] not in {"auroraview_offscreen", metadata_root}
                    or entry.flag_bits & 1
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000
                    or Path(entry.filename).suffix.lower() in {".dll", ".pyd", ".so", ".exe"}
                ):
                    raise ValueError(
                        f"Offscreen wheel contains an unsupported path: {entry.filename}"
                    )
            if not required.issubset(names):
                raise ValueError("Offscreen wheel is missing client or wheel metadata")
            metadata = BytesParser().parsebytes(archive.read(f"{metadata_root}/METADATA"))
            normalized_name = re.sub(r"[-_.]+", "-", metadata.get("Name", "")).lower()
            if (
                len(metadata.get_all("Name", [])) != 1
                or len(metadata.get_all("Version", [])) != 1
                or normalized_name != "auroraview-offscreen"
                or metadata.get("Version") != match[1]
            ):
                raise ValueError("Offscreen wheel filename and METADATA disagree")
            if metadata.get_all("Requires-Dist"):
                raise ValueError("Offscreen wheel must have no Python dependencies")
            wheel = BytesParser().parsebytes(archive.read(f"{metadata_root}/WHEEL"))
            if (
                wheel.get("Wheel-Version") != "1.0"
                or wheel.get("Root-Is-Purelib", "").lower() != "true"
                or wheel.get_all("Tag") != ["py3-none-any"]
            ):
                raise ValueError("Offscreen wheel must declare a pure-Python py3-none-any wheel")
            if archive.testzip() is not None:
                raise ValueError("Offscreen wheel failed its CRC check")
    except (zipfile.BadZipFile, KeyError) as error:
        raise ValueError("Offscreen wheel is not a valid wheel archive") from error
    return content


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


def extension_files(root: Path, *, offscreen_wheel: Path | None = None) -> dict[str, bytes]:
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
        elif (
            source.is_file()
            and relative.parts[0] == "assets"
            and source.suffix in {".html", ".css", ".js"}
        ):
            files[relative.as_posix()] = source.read_bytes()
    if "__init__.py" not in files:
        raise ValueError("Extension requires an add-on entry point")
    for name in (MANIFEST_NAME, "LICENSE"):
        source = root / name
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Extension requires a regular {name} file")
        files[name] = source.read_bytes()
    if offscreen_wheel is not None:
        name = f"wheels/{offscreen_wheel.name}"
        files[name] = wheel_bytes(offscreen_wheel)
        # Prefix the key so it stays at the top level even when the source
        # manifest later gains [permissions] or [build] tables.
        declaration = f'# Optional local wheel selected at build time.\nwheels = ["./{name}"]\n\n'
        files[MANIFEST_NAME] = declaration.encode("utf-8") + files[MANIFEST_NAME]
    return dict(sorted(files.items()))


def build_extension(
    root: Path = ROOT, output: Path | None = None, *, offscreen_wheel: Path | None = None
) -> Path:
    """Write a root-layout ZIP with fixed timestamps, permissions and ordering."""
    manifest = read_manifest(root)
    files = extension_files(root, offscreen_wheel=offscreen_wheel)
    if output is None:
        output = root / "dist" / f"{PACKAGE_ID}-{manifest['version']}.zip"
    if offscreen_wheel is not None and output.resolve() == offscreen_wheel.resolve():
        raise ValueError("Extension output must not overwrite the selected offscreen wheel")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Stored entries avoid differences between compressor versions and keep the
    # Python adapter and optional wheel reproducible across supported toolchains.
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
    parser.add_argument(
        "--offscreen-wheel", type=Path, help="Bundle a local auroraview_offscreen pure-Python wheel"
    )
    args = parser.parse_args()
    print(f"Built {build_extension(output=args.output, offscreen_wheel=args.offscreen_wheel).name}")


if __name__ == "__main__":
    main()
