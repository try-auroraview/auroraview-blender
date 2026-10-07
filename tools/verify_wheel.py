"""Check that the built wheel contains the reviewed Python-only adapter."""

from __future__ import annotations

import importlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
wheels = list((ROOT / "dist").glob("auroraview_blender-*.whl"))
assert len(wheels) == 1, f"Expected exactly one wheel, found {len(wheels)}"
wheel = wheels[0]
with zipfile.ZipFile(wheel) as archive:
    for filename in ("__init__.py", "runtime.py"):
        name = f"auroraview_blender/{filename}"
        assert archive.read(name) == (ROOT / "src" / name).read_bytes(), name
    assert not any(
        name.endswith((".so", ".pyd", ".dll", ".dylib")) for name in archive.namelist()
    ), "Native binaries must not be bundled"

sys.path.insert(0, str(wheel))
module = importlib.import_module("auroraview_blender")
assert module.__file__ is not None and str(wheel) in module.__file__
assert "bpy" not in sys.modules, "Package import must not import Blender"
assert "auroraview" not in sys.modules, "Package import must not import Core"
print("Wheel modules match source; lazy import passed without Blender or Core")
