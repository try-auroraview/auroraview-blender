"""Extension distribution contract; these checks do not launch Blender."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from build_extension import ROOT, build_extension, extension_files, read_manifest  # noqa: E402
from verify_extension import verify_extension  # noqa: E402


class ExtensionPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root / "src" / "auroraview_blender"
        self.package.mkdir(parents=True)
        for name in ("blender_manifest.toml", "LICENSE"):
            shutil.copyfile(ROOT / name, self.root / name)
        (self.package / "__init__.py").write_text("from .runtime import value\n", encoding="utf-8")
        (self.package / "runtime.py").write_text("value = 42\n", encoding="utf-8")

    def test_manifest_identifies_development_adapter_for_blender_42(self):
        manifest = read_manifest(ROOT)
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(manifest["id"], "auroraview_blender")
        self.assertEqual(manifest["blender_version_min"], "4.2.0")
        self.assertEqual(manifest["license"], ["SPDX:MIT"])
        self.assertNotIn("wheels", manifest)

    def test_builder_flattens_addon_and_includes_new_modules_only(self):
        (self.package / "panels.py").write_text("label = 'native'\n", encoding="utf-8")
        (self.package / "__pycache__").mkdir()
        (self.package / "__pycache__" / "runtime.py").write_text("cache", encoding="utf-8")
        for name in ("runtime.pyc", "core.whl", "native.dll", "report.json"):
            (self.package / name).write_bytes(b"not adapter source")
        archive_path = build_extension(self.root)
        verify_extension(archive_path, self.root)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(
                archive.namelist(),
                ["LICENSE", "__init__.py", "blender_manifest.toml", "panels.py", "runtime.py"],
            )
            self.assertEqual(archive.read("panels.py"), (self.package / "panels.py").read_bytes())

    def test_build_is_deterministic_despite_source_timestamps(self):
        first = build_extension(self.root, self.root / "first.zip").read_bytes()
        (self.package / "runtime.py").touch()
        second = build_extension(self.root, self.root / "second.zip").read_bytes()
        self.assertEqual(first, second)

    def test_missing_entry_point_is_rejected(self):
        (self.package / "__init__.py").unlink()
        with self.assertRaisesRegex(ValueError, "entry point"):
            extension_files(self.root)

    def test_dependency_bundle_manifest_is_rejected(self):
        with (self.root / "blender_manifest.toml").open("a", encoding="utf-8") as manifest:
            manifest.write('\nwheels = ["./core.whl"]\n')
        with self.assertRaisesRegex(ValueError, "bundle dependencies"):
            build_extension(self.root)

    def test_symlinked_module_is_rejected(self):
        source = self.package / "linked.py"
        try:
            source.symlink_to(self.package / "runtime.py")
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation is unavailable for this account")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            extension_files(self.root)

    def test_verify_rejects_extra_or_changed_entries(self):
        archive_path = build_extension(self.root)
        with zipfile.ZipFile(archive_path, "a") as archive:
            archive.writestr("../outside.py", "unwanted")
        with self.assertRaisesRegex(ValueError, "selected source files"):
            verify_extension(archive_path, self.root)
        archive_path = build_extension(self.root)
        (self.package / "runtime.py").write_text("value = 43\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "differs from source"):
            verify_extension(archive_path, self.root)


if __name__ == "__main__":
    unittest.main()
