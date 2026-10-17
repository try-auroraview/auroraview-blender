"""Actual Blender scene contracts, independent of Web rendering and GUI input.

Run only in a disposable factory-startup process:
blender --background --factory-startup --python-exit-code 1 --python tests/blender_scene_probe.py
"""

import math
import sys
import tempfile
import unittest
from pathlib import Path

import bpy
from mathutils import Euler, Quaternion

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from auroraview_blender.scene_tools import BlenderSceneAdapter  # noqa: E402


class BlenderSceneTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.adapter = BlenderSceneAdapter(bpy)
        self.cube = self.object("Cube")
        self.other = self.object("Other")
        self.cube.select_set(True)
        bpy.context.view_layer.objects.active = self.cube

    def object(self, name, collection=None):
        mesh = bpy.data.meshes.new(name + "Mesh")
        obj = bpy.data.objects.new(name, mesh)
        (collection or bpy.context.scene.collection).objects.link(obj)
        bpy.context.view_layer.update()
        return obj

    def call(self, tool, **params):
        return self.adapter.execute("blender." + tool, params)

    def assert_orientation(self, actual, expected):
        self.assertAlmostEqual(abs(actual.normalized().dot(expected.normalized())), 1.0, places=5)

    def test_rotation_updates_every_active_representation(self):
        angles = (0.2, -0.4, 1.1)
        for mode in ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX", "QUATERNION", "AXIS_ANGLE"):
            with self.subTest(mode=mode):
                self.cube.rotation_mode = mode
                result = self.call("object.transform", name="Cube", rotation=list(angles))
                bpy.context.view_layer.update()
                order = mode if len(mode) == 3 else "XYZ"
                expected = Euler(angles, order).to_quaternion()
                self.assert_orientation(self.cube.matrix_local.to_quaternion(), expected)
                self.assert_orientation(Euler(result["rotation"], order).to_quaternion(), expected)
                self.assertEqual(result["rotation_mode"], mode)
                self.assertEqual(self.cube.rotation_mode, mode)

    def test_readback_ignores_inactive_euler_values(self):
        expected = Euler((0.3, 0.2, -0.4), "XYZ").to_quaternion()
        for mode in ("QUATERNION", "AXIS_ANGLE"):
            with self.subTest(mode=mode):
                self.cube.rotation_mode = mode
                self.cube.rotation_euler = (1, 1, 1)
                if mode == "QUATERNION":
                    self.cube.rotation_quaternion = expected
                else:
                    axis, angle = expected.to_axis_angle()
                    self.cube.rotation_axis_angle = (angle, *axis)
                row = next(
                    row for row in self.adapter.get_context()["objects"] if row["name"] == "Cube"
                )
                self.assert_orientation(Euler(row["rotation"], "XYZ").to_quaternion(), expected)

    def assert_selection_rejected(self, target, pattern):
        before = self.adapter.revision
        with self.assertRaisesRegex(ValueError, pattern):
            self.call("selection.set", names=[target.name])
        self.assertEqual([obj.name for obj in bpy.context.selected_objects], ["Cube"])
        self.assertIs(bpy.context.view_layer.objects.active, self.cube)
        self.assertEqual(self.adapter.revision, before)
        self.assertFalse(self.adapter.get_audit_log()[-1]["ok"])

    def test_hidden_selection_is_rejected_without_losing_current_selection(self):
        self.other.hide_set(True)
        self.assert_selection_rejected(self.other, "visible and selectable")

    def test_object_selection_lock_is_rejected(self):
        self.other.hide_select = True
        self.assert_selection_rejected(self.other, "visible and selectable")

    def test_collection_selection_lock_rolls_back_silent_host_rejection(self):
        collection = bpy.data.collections.new("Locked")
        bpy.context.scene.collection.children.link(collection)
        locked = self.object("LockedObject", collection)
        collection.hide_select = True
        self.assertTrue(locked.visible_get(view_layer=bpy.context.view_layer))
        self.assertFalse(locked.hide_select)
        self.assert_selection_rejected(locked, "could not select")
        with self.assertRaisesRegex(ValueError, "could not select"):
            self.call("selection.set", names=["Other", "LockedObject"])
        self.assertEqual([obj.name for obj in bpy.context.selected_objects], ["Cube"])
        self.assertIs(bpy.context.view_layer.objects.active, self.cube)

    def test_excluded_view_layer_object_is_rejected(self):
        collection = bpy.data.collections.new("Excluded")
        bpy.context.scene.collection.children.link(collection)
        excluded = self.object("ExcludedObject", collection)
        bpy.context.view_layer.layer_collection.children[collection.name].exclude = True
        self.assert_selection_rejected(excluded, "active view layer")

    def test_edit_mode_selection_fails_before_mutation(self):
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            self.assert_selection_rejected(self.other, "Object Mode")
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")

    def test_selection_readback_matches_host_and_deduplicates(self):
        result = self.call("selection.set", names=["Other", "Other"], active="Other")
        self.assertEqual(result, {"selected": ["Other"], "active": "Other"})
        self.assertEqual(result["selected"], [obj.name for obj in bpy.context.selected_objects])
        self.assertEqual(self.call("selection.set", names=[]), {"selected": [], "active": None})
        self.assertFalse(bpy.context.selected_objects)

    def test_invalid_transform_does_not_partially_apply_location(self):
        self.cube.rotation_mode = "QUATERNION"
        before = self.cube.rotation_quaternion.copy()
        with self.assertRaises(ValueError):
            self.call(
                "object.transform", name="Cube", location=[4, 5, 6], rotation=[0, math.nan, 0]
            )
        self.assertEqual(tuple(self.cube.location), (0, 0, 0))
        self.assertEqual(self.cube.rotation_quaternion, before)
        self.assertEqual(self.adapter.revision, 0)

    def test_rotation_survives_save_reopen_with_host_readback(self):
        self.cube.rotation_mode = "AXIS_ANGLE"
        self.call("object.transform", name="Cube", rotation=[0.3, 0.4, 0.5])
        expected = Euler((0.3, 0.4, 0.5), "XYZ").to_quaternion()
        with tempfile.TemporaryDirectory(prefix="auroraview-scene-probe-") as directory:
            path = str(Path(directory) / "scene.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            obj = bpy.data.objects["Cube"]
            self.assertEqual(obj.rotation_mode, "AXIS_ANGLE")
            angle, *axis = obj.rotation_axis_angle
            self.assert_orientation(Quaternion(axis, angle), expected)
            result = next(
                row for row in self.adapter.get_context()["objects"] if row["name"] == "Cube"
            )
            self.assert_orientation(Euler(result["rotation"], "XYZ").to_quaternion(), expected)


if __name__ == "__main__":
    if not bpy.app.background:
        raise RuntimeError("Run this destructive scene probe in an isolated background Blender")
    print("BLENDER_SCENE_PROBE", bpy.app.version_string, sys.version, flush=True)
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(BlenderSceneTests)
    )
    if not result.wasSuccessful():
        raise RuntimeError("Blender scene contract probe failed")
