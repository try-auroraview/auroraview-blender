"""Scene contracts reject invalid mutations before touching host objects."""

import math
import threading
import unittest
from types import SimpleNamespace

from auroraview_blender.scene_tools import BlenderSceneAdapter


class Object:
    def __init__(self, name):
        self.name, self.type, self.selected = name, "MESH", False
        self.location, self.rotation_euler, self.scale = (0, 0, 0), (0, 0, 0), (1, 1, 1)

    def select_get(self):
        return self.selected

    def select_set(self, value):
        self.selected = value


class Objects(list):
    active = None

    def get(self, name):
        return next((obj for obj in self if obj.name == name), None)


def host(count=3):
    objects = Objects(Object(f"Object{index}") for index in range(count))
    scene = SimpleNamespace(name="Scene", objects=objects, frame_current=1)
    scene.frame_set = lambda value: setattr(scene, "frame_current", value)
    bpy = SimpleNamespace(
        app=SimpleNamespace(version_string="contract-host"),
        context=SimpleNamespace(
            scene=scene, selected_objects=[], view_layer=SimpleNamespace(objects=objects)
        ),
        data=SimpleNamespace(objects=objects),
    )
    return bpy, BlenderSceneAdapter(bpy)


class SceneContractTests(unittest.TestCase):
    def test_bounded_context_and_detached_manifest(self):
        bpy, adapter = host(300)
        result = adapter.get_context()
        self.assertEqual(len(result["objects"]), 64)
        self.assertTrue(result["truncated"])
        tools = adapter.list_tools()
        tools[0]["name"] = "corrupt"
        self.assertEqual(adapter.list_tools()[0]["name"], "blender.capabilities")
        self.assertEqual(len(bpy.context.scene.objects), 300)

    def test_transform_validation_is_transactional(self):
        bpy, adapter = host()
        for bad in ([1, math.nan, 3], [1, 2], [True, 2, 3], [1e8, 2, 3]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                adapter.execute(
                    "blender.object.transform",
                    {
                        "name": "Object0",
                        "location": [4, 5, 6],
                        "rotation": bad,
                    },
                )
            self.assertEqual(bpy.data.objects[0].location, (0, 0, 0))
        result = adapter.execute(
            "blender.object.transform",
            {
                "name": "Object0",
                "location": [4, 5, 6],
                "rotation": [0, 0, 0.5],
            },
        )
        self.assertEqual(result["location"], [4, 5, 6])
        self.assertEqual(adapter.revision, 1)

    def test_invalid_selection_does_not_clear_existing(self):
        bpy, adapter = host()
        obj = bpy.data.objects[0]
        obj.selected = True
        bpy.context.selected_objects = [obj]
        for params in ({"names": ["missing"]}, {"names": ["Object1"], "active": "Object2"}):
            with self.assertRaises(ValueError):
                adapter.execute("blender.selection.set", params)
            self.assertTrue(obj.selected)
        result = adapter.execute("blender.selection.set", {"names": ["Object1"]})
        self.assertFalse(obj.selected)
        self.assertEqual(result, {"selected": ["Object1"], "active": "Object1"})

    def test_unknown_tools_kwargs_names_and_collision_are_refused(self):
        bpy, adapter = host()
        for name, params in (
            ("eval", {}),
            ("blender.scene.describe", {"limit": True}),
            ("blender.object.rename", {"name": "Object0", "new_name": "Object1"}),
            ("blender.object.rename", {"name": "Object0", "new_name": ""}),
            ("blender.scene.describe", {"script": "arbitrary"}),
        ):
            with self.subTest(name=name, params=params), self.assertRaises(ValueError):
                adapter.execute(name, params)
        self.assertEqual(bpy.data.objects[0].name, "Object0")

    def test_worker_cannot_access_scene(self):
        _, adapter = host()
        errors = []

        def worker():
            try:
                adapter.get_context()
            except RuntimeError as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2)
        self.assertEqual(len(errors), 1)
        self.assertIn("main thread", errors[0])

    def test_audit_budget_and_frame_validation(self):
        _, adapter = host()
        with self.assertRaises(ValueError):
            adapter.execute("blender.timeline.set_frame", {"frame": 1.5})
        for frame in range(80):
            adapter.execute("blender.timeline.set_frame", {"frame": frame})
        self.assertEqual(len(adapter.get_audit_log()), 64)
        self.assertEqual(adapter.get_context()["frame"], 79)


if __name__ == "__main__":
    unittest.main()
