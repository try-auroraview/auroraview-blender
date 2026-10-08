"""Optional wheel packaging uses Blender's declarative extension installation."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from build_extension import ROOT, build_extension, tomllib  # noqa: E402
from verify_extension import verify_extension  # noqa: E402


class OffscreenWheelPackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package = self.root / "src" / "auroraview_blender"
        self.package.mkdir(parents=True)
        (self.package / "__init__.py").write_text("# adapter\n", encoding="utf-8")
        for name in ("blender_manifest.toml", "LICENSE"):
            shutil.copyfile(ROOT / name, self.root / name)
        self.wheel = self.root / "auroraview_offscreen-0.1.0.dev0-py3-none-any.whl"
        self.dist_info = "auroraview_offscreen-0.1.0.dev0.dist-info"
        self.make_wheel()

    def make_wheel(self, *, metadata="", wheel_metadata="", extra=None):
        with zipfile.ZipFile(self.wheel, "w") as archive:
            archive.writestr("auroraview_offscreen/__init__.py", "# client\n")
            archive.writestr(
                f"{self.dist_info}/METADATA",
                "Metadata-Version: 2.3\nName: auroraview-offscreen\nVersion: 0.1.0.dev0\n"
                "Requires-Python: >=3.10\n" + metadata,
            )
            archive.writestr(
                f"{self.dist_info}/WHEEL",
                wheel_metadata or "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            )
            archive.writestr(f"{self.dist_info}/RECORD", "")
            for name, value in (extra or {}).items():
                archive.writestr(name, value)

    def test_optional_wheel_is_unmodified_and_declared_in_root_manifest(self):
        original_manifest = (self.root / "blender_manifest.toml").read_bytes()
        with (self.root / "blender_manifest.toml").open("a", encoding="utf-8") as source:
            source.write('\n[permissions]\nfiles = "Read a maintainer selected runtime bundle"\n')
        source_manifest = (self.root / "blender_manifest.toml").read_bytes()
        output = build_extension(self.root, offscreen_wheel=self.wheel)
        verify_extension(output, self.root, offscreen_wheel=self.wheel)
        with zipfile.ZipFile(output) as archive:
            manifest = tomllib.loads(archive.read("blender_manifest.toml").decode("utf-8"))
            name = f"wheels/{self.wheel.name}"
            self.assertEqual(manifest["wheels"], [f"./{name}"])
            self.assertNotIn("wheels", manifest["permissions"])
            self.assertEqual(archive.read(name), self.wheel.read_bytes())
            self.assertEqual(archive.namelist(), sorted(archive.namelist()))
        self.assertEqual((self.root / "blender_manifest.toml").read_bytes(), source_manifest)
        self.assertNotEqual(source_manifest, original_manifest)
        second = build_extension(self.root, self.root / "second.zip", offscreen_wheel=self.wheel)
        self.assertEqual(output.read_bytes(), second.read_bytes())

    def test_default_build_remains_without_wheels(self):
        output = build_extension(self.root)
        verify_extension(output, self.root)
        with zipfile.ZipFile(output) as archive:
            manifest = tomllib.loads(archive.read("blender_manifest.toml").decode("utf-8"))
            self.assertNotIn("wheels", manifest)
            self.assertFalse(any(name.startswith("wheels/") for name in archive.namelist()))

    def test_wheel_requires_explicit_selection_during_verification(self):
        output = build_extension(self.root, offscreen_wheel=self.wheel)
        with self.assertRaisesRegex(ValueError, "selected source files"):
            verify_extension(output, self.root)
        self.make_wheel(extra={"auroraview_offscreen/changed.py": "# changed"})
        with self.assertRaisesRegex(ValueError, "differs from source"):
            verify_extension(output, self.root, offscreen_wheel=self.wheel)

    def test_dependency_or_binary_wheels_fail_before_output_is_created(self):
        output = self.root / "invalid.zip"
        for options, reason in (
            ({"metadata": "Requires-Dist: network-package\n"}, "no Python dependencies"),
            ({"wheel_metadata": "Wheel-Version: 1.0\nRoot-Is-Purelib: false\n"}, "pure-Python"),
            ({"extra": {"auroraview_offscreen/native.pyd": b"native"}}, "unsupported path"),
            ({"extra": {"../escaped.py": "bad"}}, "unsupported path"),
            ({"extra": {"auroraview_offscreen/../escaped.py": "bad"}}, "unsupported path"),
        ):
            with self.subTest(options=options):
                self.make_wheel(**options)
                with self.assertRaisesRegex(ValueError, reason):
                    build_extension(self.root, output, offscreen_wheel=self.wheel)
                self.assertFalse(output.exists())

    def test_metadata_and_filename_must_identify_the_same_standalone_client(self):
        self.make_wheel(metadata="Name: something-else\n")
        # Multiple identity headers are rejected instead of relying on parser order.
        with self.assertRaisesRegex(ValueError, "METADATA"):
            build_extension(self.root, offscreen_wheel=self.wheel)
        invalid = self.wheel.with_name("auroraview_core-1.0.0-py3-none-any.whl")
        shutil.copyfile(self.wheel, invalid)
        with self.assertRaisesRegex(ValueError, "must be auroraview_offscreen"):
            build_extension(self.root, offscreen_wheel=invalid)

    def test_cli_bundles_and_verifies_a_local_wheel_without_installation(self):
        output = self.root / "cli.zip"
        for tool, positional in (
            ("build_extension.py", ["--output", str(output)]),
            ("verify_extension.py", [str(output)]),
        ):
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / tool),
                    *positional,
                    "--offscreen-wheel",
                    str(self.wheel),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.read(f"wheels/{self.wheel.name}"), self.wheel.read_bytes())


if __name__ == "__main__":
    unittest.main()
