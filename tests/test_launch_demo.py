"""The task-owned stop marker keeps cleanup retries on Blender's timer."""

import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class LaunchDemoTests(unittest.TestCase):
    def test_cleanup_retry_precedes_scene_access_and_quits_only_after_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "state.json"
            evidence.with_suffix(".stop").touch()
            addon = Mock(unregister=Mock(side_effect=[RuntimeError("cleanup pending"), None]))
            bpy = SimpleNamespace(
                context=SimpleNamespace(preferences=SimpleNamespace(view=SimpleNamespace())),
                app=SimpleNamespace(timers=SimpleNamespace(register=Mock())),
                ops=SimpleNamespace(wm=SimpleNamespace(quit_blender=Mock())),
            )
            argv = [
                "launch_demo.py",
                "--",
                "--renderer-bundle",
                str(root),
                "--client",
                str(root),
                "--evidence",
                str(evidence),
            ]
            original_path = sys.path[:]
            try:
                with (
                    patch.dict(sys.modules, {"bpy": bpy, "auroraview_blender": addon}),
                    patch.object(sys, "argv", argv),
                    patch.dict(os.environ),
                ):
                    script = runpy.run_path(
                        str(Path(__file__).resolve().parents[1] / "tools" / "launch_demo.py")
                    )
                    self.assertEqual(script["record"](), 0.25)
                    self.assertIn("cleanup pending", evidence.read_text(encoding="utf-8"))
                    bpy.ops.wm.quit_blender.assert_not_called()
                    self.assertIsNone(script["record"]())
                    bpy.ops.wm.quit_blender.assert_called_once()
                    addon.get_agent_adapter.assert_not_called()
                    addon.open_editor.assert_not_called()
            finally:
                sys.path[:] = original_path


if __name__ == "__main__":
    unittest.main()
