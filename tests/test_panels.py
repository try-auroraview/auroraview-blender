"""Native panel ownership with explicit bpy doubles, not Blender GUI certification."""

import importlib.util
import sys
import threading
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from test_runtime import fake_bpy

from auroraview_blender.panels import NativePanelRegistry, draw_selection_status
from auroraview_blender.runtime import require_main_thread


class NativePanelTests(unittest.TestCase):
    def setUp(self):
        self.bpy = fake_bpy()

        def register(panel_type):
            self.bpy.classes.append(panel_type)
            setattr(self.bpy.types, panel_type.bl_idname, panel_type)

        def unregister(panel_type):
            self.bpy.classes.remove(panel_type)
            delattr(self.bpy.types, panel_type.bl_idname)

        self.bpy.utils.register_class = register
        self.bpy.utils.unregister_class = unregister
        self.registry = NativePanelRegistry(self.bpy)

    def tearDown(self):
        self.registry.clear()

    def run_worker(self, callback):
        errors = []

        def run():
            try:
                callback()
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        return errors

    def test_register_draw_and_unregister_native_panel(self):
        draw = Mock()
        panel_type = self.registry.register("EXAMPLE_PT_inspector", "Inspector", draw)
        self.assertEqual(self.bpy.classes, [panel_type])
        self.assertEqual(panel_type.bl_space_type, "VIEW_3D")
        self.assertEqual(panel_type.bl_region_type, "UI")
        self.assertEqual(panel_type.bl_category, "AuroraView")
        self.assertEqual(panel_type.__module__, "auroraview_blender.panels")
        panel = panel_type()
        panel.layout = object()
        context = object()
        panel.draw(context)
        draw.assert_called_once_with(panel.layout, context)
        self.assertTrue(self.registry.unregister("EXAMPLE_PT_inspector"))
        self.assertFalse(self.registry.unregister("EXAMPLE_PT_inspector"))
        self.assertEqual(self.bpy.classes, [])

    def test_custom_category_and_duplicate_id(self):
        first = self.registry.register(
            "EXAMPLE_PT_inspector", "Inspector", Mock(), category="Custom tools"
        )
        self.assertEqual(first.bl_category, "Custom tools")
        with self.assertRaisesRegex(ValueError, "already registered"):
            self.registry.register("EXAMPLE_PT_inspector", "Other", Mock())
        self.assertEqual(self.bpy.classes, [first])

    def test_second_registry_cannot_replace_an_existing_host_panel(self):
        original_draw = Mock()
        first = self.registry.register("EXAMPLE_PT_shared", "First owner", original_draw)
        second_registry = NativePanelRegistry(self.bpy)
        original_register = self.bpy.utils.register_class
        self.bpy.utils.register_class = Mock(wraps=original_register)
        with self.assertRaisesRegex(ValueError, "already registered in Blender"):
            second_registry.register("EXAMPLE_PT_shared", "Second owner", Mock())
        self.bpy.utils.register_class.assert_not_called()
        self.assertFalse(second_registry.unregister("EXAMPLE_PT_shared"))
        second_registry.clear()
        self.assertEqual(self.bpy.classes, [first])
        self.assertIs(self.bpy.types.EXAMPLE_PT_shared, first)
        panel = first()
        panel.layout = object()
        context = object()
        panel.draw(context)
        original_draw.assert_called_once_with(panel.layout, context)

    def test_generated_panel_keeps_blender_extension_module_namespace(self):
        package_name = "bl_ext.example_repository.auroraview_blender"
        module_name = package_name + ".panels"
        runtime_name = package_name + ".runtime"
        runtime_module = ModuleType(runtime_name)
        runtime_module.require_main_thread = require_main_thread
        path = Path(__file__).resolve().parents[1] / "src" / "auroraview_blender" / "panels.py"
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {runtime_name: runtime_module, module_name: module}):
            spec.loader.exec_module(module)
            registry = module.NativePanelRegistry(self.bpy)
            try:
                panel_type = registry.register("EXAMPLE_PT_extension", "Extension", Mock())
                self.assertEqual(panel_type.__module__, module_name)
            finally:
                registry.clear()

    def test_invalid_definition_rejected_before_registration(self):
        for panel_id in [
            None,
            "",
            "tool",
            "example_PT_tool",
            "EXAMPLE_PT_x.y",
            "EXAMPLE_PT_" + "a" * 64,
        ]:
            with self.subTest(panel_id=panel_id):
                with self.assertRaisesRegex(ValueError, "PREFIX_PT_name"):
                    self.registry.register(panel_id, "Tool", Mock())
        for title in [None, "", " "]:
            with self.assertRaisesRegex(ValueError, "title"):
                self.registry.register("EXAMPLE_PT_tool", title, Mock())
        for category in [None, "", " "]:
            with self.assertRaisesRegex(ValueError, "category"):
                self.registry.register("EXAMPLE_PT_tool", "Tool", Mock(), category=category)
        with self.assertRaisesRegex(TypeError, "draw"):
            self.registry.register("EXAMPLE_PT_tool", "Tool", None)
        self.assertEqual(self.bpy.classes, [])

    def test_failed_registration_has_no_owned_class(self):
        self.bpy.utils.register_class = Mock(side_effect=RuntimeError("registration failed"))
        with self.assertRaisesRegex(RuntimeError, "registration failed"):
            self.registry.register("EXAMPLE_PT_tool", "Tool", Mock())
        self.assertFalse(self.registry.unregister("EXAMPLE_PT_tool"))

    def test_failed_unregister_retains_class_for_retry(self):
        panel_type = self.registry.register("EXAMPLE_PT_tool", "Tool", Mock())
        original = self.bpy.utils.unregister_class
        self.bpy.utils.unregister_class = Mock(side_effect=RuntimeError("busy"))
        with self.assertRaisesRegex(RuntimeError, "busy"):
            self.registry.unregister("EXAMPLE_PT_tool")
        with self.assertRaisesRegex(ValueError, "already registered"):
            self.registry.register("EXAMPLE_PT_tool", "Tool", Mock())
        self.assertEqual(self.bpy.classes, [panel_type])
        self.bpy.utils.unregister_class = original
        self.assertTrue(self.registry.unregister("EXAMPLE_PT_tool"))

    def test_clear_removes_other_panels_and_retries_failed_cleanup(self):
        first = self.registry.register("EXAMPLE_PT_first", "First", Mock())
        second = self.registry.register("EXAMPLE_PT_second", "Second", Mock())
        third = self.registry.register("EXAMPLE_PT_third", "Third", Mock())
        original = self.bpy.utils.unregister_class
        attempted = []

        def unregister(panel_type):
            attempted.append(panel_type)
            if panel_type is second:
                raise RuntimeError("busy")
            original(panel_type)

        self.bpy.utils.unregister_class = unregister
        with self.assertRaisesRegex(RuntimeError, "native panels") as caught:
            self.registry.clear()
        self.assertEqual(str(caught.exception.__cause__), "busy")
        self.assertEqual(attempted, [third, second, first])
        self.assertEqual(self.bpy.classes, [second])
        self.bpy.utils.unregister_class = original
        self.registry.clear()
        self.registry.clear()
        self.assertEqual(self.bpy.classes, [])

    def test_worker_cannot_register_unregister_clear_or_draw(self):
        draw = Mock()
        panel_type = self.registry.register("EXAMPLE_PT_tool", "Tool", draw)
        panel = panel_type()
        panel.layout = object()
        callbacks = [
            lambda: self.registry.register("EXAMPLE_PT_worker", "Worker", Mock()),
            lambda: self.registry.unregister("EXAMPLE_PT_tool"),
            self.registry.clear,
            lambda: panel.draw(object()),
            lambda: draw_selection_status(object(), object()),
        ]
        for callback in callbacks:
            with self.subTest(callback=callback):
                errors = self.run_worker(callback)
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], RuntimeError)
                self.assertIn("main thread", str(errors[0]))
        draw.assert_not_called()
        self.assertEqual(self.bpy.classes, [panel_type])

    def test_builtin_draws_live_native_properties(self):
        layout = Mock()
        active = SimpleNamespace(name="Cube", location=(1, 2, 3))
        context = SimpleNamespace(mode="OBJECT", selected_objects=[active], active_object=active)
        draw_selection_status(layout, context)
        layout.label.assert_any_call(text="Selected objects: 1")
        layout.label.assert_any_call(text="Mode: OBJECT")
        self.assertEqual(layout.prop.call_args_list[0].args, (active, "name"))
        self.assertEqual(layout.prop.call_args_list[1].args, (active, "location"))
        self.assertEqual(layout.prop.call_args_list[2].args, (active, "rotation_euler"))
        self.assertEqual(layout.prop.call_args_list[3].args, (active, "scale"))
        for mode, property_name in [
            ("QUATERNION", "rotation_quaternion"),
            ("AXIS_ANGLE", "rotation_axis_angle"),
        ]:
            with self.subTest(mode=mode):
                active.rotation_mode = mode
                layout.reset_mock()
                draw_selection_status(layout, context)
                self.assertEqual(layout.prop.call_args_list[2].args, (active, property_name))
        layout.reset_mock()
        context.active_object = None
        context.selected_objects = []
        draw_selection_status(layout, context)
        layout.label.assert_any_call(text="Selected objects: 0")
        layout.label.assert_any_call(text="No active object", icon="INFO")
        layout.prop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
