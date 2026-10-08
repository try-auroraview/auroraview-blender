"""Native area ownership with explicit bpy doubles, not a GPU/GUI certification."""

import itertools
import sys
import threading
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from auroraview_blender import surfaces
from auroraview_blender.surfaces import NativeSurfaceManager, operator_classes, surface_capabilities


class Pointer:
    ids = itertools.count(1)

    def __init__(self, **kwargs):
        self.pointer = next(self.ids)
        self.__dict__.update(kwargs)

    def as_pointer(self):
        return self.pointer


class Area(Pointer):
    def __init__(self, x=100, y=100, width=100, height=80):
        super().__init__()
        self._type = "VIEW_3D"
        self.spaces = SimpleNamespace(active=Pointer())
        self.regions = [
            Pointer(type="WINDOW", x=x, y=y, width=width, height=height),
            Pointer(type="HEADER", x=x, y=y + height, width=width, height=20),
        ]
        self.tag_redraw = Mock()

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, value):
        if value != self._type:
            self.spaces.active = Pointer()
        self._type = value


class Host:
    def __init__(self):
        area = Area()
        window = Pointer(screen=SimpleNamespace(areas=[area]))
        wm = SimpleNamespace(windows=[window], modal_handler_add=Mock())
        self.context = SimpleNamespace(
            window=window, area=area, region=area.regions[0], window_manager=wm
        )

        @contextmanager
        def temp_override(**kwargs):
            before = {key: getattr(self.context, key) for key in kwargs}
            self.context.__dict__.update(kwargs)
            try:
                yield self.context
            finally:
                self.context.__dict__.update(before)

        self.context.temp_override = temp_override

        def split(**kwargs):
            self.context.window.screen.areas.append(Area(x=200))
            return {"FINISHED"}

        self.bpy = SimpleNamespace(
            context=self.context,
            app=SimpleNamespace(background=False),
            types=SimpleNamespace(
                Operator=object,
                SpaceImageEditor=SimpleNamespace(
                    draw_handler_add=Mock(return_value=object()), draw_handler_remove=Mock()
                ),
            ),
            ops=SimpleNamespace(
                screen=SimpleNamespace(area_split=Mock(side_effect=split)),
                auroraview=SimpleNamespace(surface_input=Mock(return_value={"RUNNING_MODAL"})),
            ),
        )


class Renderer:
    def __init__(self):
        self.alive = True
        self.closed = False
        self.messages = []
        self.polls = 0
        self.stopping = False
        self.create = Mock()
        self.resize = Mock()
        self.input = Mock()
        self.close_surface = Mock()
        self.shutdown = Mock(side_effect=self._shutdown)
        self.terminate = Mock(side_effect=self._terminate)

    def poll(self):
        self.polls += 1
        if self.stopping:
            self.alive = False
            self.closed = True
        messages, self.messages = self.messages, []
        return messages

    def _shutdown(self):
        self.stopping = True

    def _terminate(self):
        self.alive = False
        self.closed = True


def event(kind="LEFTMOUSE", value="PRESS", x=120, y=120, **kwargs):
    return SimpleNamespace(type=kind, value=value, mouse_x=x, mouse_y=y, **kwargs)


class NativeSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.renderer = Renderer()
        self.messages = []
        self.manager = NativeSurfaceManager(
            self.host.bpy, lambda: self.renderer, on_message=self.messages.append
        )
        self.capability = patch.object(
            surfaces, "surface_capabilities", return_value={"native_offscreen_surface": True}
        )
        self.capability.start()

    def tearDown(self):
        self.manager.stop(force=True)
        self.capability.stop()

    def open(self, **kwargs):
        return self.manager.open(self.host.context, split=False, html="<input>", **kwargs)

    def frame(self, surface_id, **kwargs):
        surface = self.manager.surfaces[surface_id]
        message = dict(
            type="frame",
            surface_id=surface_id,
            generation=surface.generation,
            format="rgba8",
            alpha="straight",
            origin="top-left",
            width=surface.width,
            height=surface.height,
            stride=surface.width * 4,
            seq=1,
            resize_revision=surface.resize_revision,
            payload=bytes(surface.width * surface.height * 4),
        )
        message.update(kwargs)
        return message

    def second_area(self, *, second_window=False):
        area = Area(x=200)
        if second_window:
            window = Pointer(screen=SimpleNamespace(areas=[area]))
            self.host.context.window_manager.windows.append(window)
            self.host.context.window = window
        else:
            self.host.context.window.screen.areas.append(area)
        self.host.context.area, self.host.context.region = area, area.regions[0]
        return area

    def test_native_split_create_and_restore_only_owned_area(self):
        original = self.host.context.area
        surface_id = self.manager.open(self.host.context, split=True, html="<input>")
        owned = self.host.context.window.screen.areas[1]
        self.assertEqual(original.type, "VIEW_3D")
        self.assertEqual(owned.type, "IMAGE_EDITOR")
        self.host.bpy.types.SpaceImageEditor.draw_handler_add.assert_called_once_with(
            self.manager._draw, (), "WINDOW", "POST_PIXEL"
        )
        self.assertEqual(self.renderer.create.call_args.kwargs["width"], 100)
        self.manager.close(surface_id)
        self.assertEqual(owned.type, "VIEW_3D")
        self.assertTrue(self.manager.needs_tick)
        self.assertFalse(self.manager.tick())
        self.assertEqual(self.renderer.polls, 1)

    def test_multiple_areas_and_windows_share_process_and_one_modal_per_window(self):
        first = self.open()
        self.second_area()
        second = self.open()
        self.assertEqual(self.host.bpy.ops.auroraview.surface_input.call_count, 1)
        self.second_area(second_window=True)
        third = self.open()
        self.assertEqual(self.host.bpy.ops.auroraview.surface_input.call_count, 2)
        self.assertEqual(len(self.manager.surfaces), 3)
        self.manager.close(first)
        self.manager.close(second)
        self.renderer.shutdown.assert_not_called()
        self.manager.close(third)
        self.renderer.shutdown.assert_called_once()
        self.assertEqual(len(self.manager.surfaces), 0)

    def test_area_or_window_disappearance_closes_without_retaining_rna(self):
        surface_id = self.open()
        area = self.host.context.area
        self.host.context.window.screen.areas.clear()
        self.manager.tick()
        self.assertNotIn(surface_id, self.manager.surfaces)
        self.assertEqual(area.type, "IMAGE_EDITOR")
        self.renderer.close_surface.assert_called_once()

    def test_user_type_or_active_space_change_is_not_reverted(self):
        self.open()
        area = self.host.context.area
        area.type = "TEXT_EDITOR"
        self.manager.tick()
        self.assertEqual(area.type, "TEXT_EDITOR")
        self.assertFalse(self.manager.surfaces)
        area.type = "VIEW_3D"
        self.open()
        replacement = Pointer()
        area.spaces.active = replacement
        self.manager.tick()
        self.assertIs(area.spaces.active, replacement)
        self.assertEqual(area.type, "IMAGE_EDITOR")

    def test_frame_generation_size_sequence_and_layout_are_validated(self):
        surface_id = self.open()
        surface = self.manager.surfaces[surface_id]
        invalid = (
            dict(generation=surface.generation + 1),
            dict(width=99),
            dict(resize_revision=1),
            dict(format="bgra8"),
            dict(alpha="premultiplied"),
            dict(origin="bottom-left"),
            dict(seq="1"),
            dict(seq=0),
            dict(seq=True),
            dict(stride=1),
            dict(payload=b"bad"),
        )
        for changes in invalid:
            self.renderer.messages.append(self.frame(surface_id, **changes))
        self.manager.tick()
        self.assertEqual(surface.frame_count, 0)
        self.renderer.messages.extend(
            [self.frame(surface_id), self.frame(surface_id, seq=5), self.frame(surface_id, seq=4)]
        )
        self.manager.tick()
        self.assertEqual(surface.sequence, 5)
        self.assertEqual(surface.frame_count, 2)
        self.assertEqual(self.manager.get_info(surface_id)["sequence"], 5)

    def test_resize_invalidates_old_revision_and_preserves_bounded_latest_frame(self):
        surface_id = self.open()
        surface = self.manager.surfaces[surface_id]
        self.renderer.messages = [self.frame(surface_id)]
        self.manager.tick()
        self.host.context.region.width = 200
        self.manager.tick()
        self.assertIsNone(surface.frame)
        self.assertEqual(surface.resize_revision, 1)
        self.renderer.resize.assert_called_once_with(
            surface_id, surface.generation, width=200, height=80
        )
        self.renderer.messages = [self.frame(surface_id, resize_revision=0, seq=2)]
        self.manager.tick()
        self.assertIsNone(surface.frame)
        self.renderer.messages = [self.frame(surface_id, seq=3)]
        self.manager.tick()
        self.assertEqual(surface.sequence, 3)

    def test_blender_ui_scale_sets_logical_viewport_and_input_mapping(self):
        self.host.context.preferences = SimpleNamespace(system=SimpleNamespace(ui_scale=2.0))
        surface_id = self.open()
        surface = self.manager.surfaces[surface_id]
        self.assertEqual((surface.width, surface.height), (50, 40))
        self.assertEqual(self.manager.get_info(surface_id)["ui_scale"], 2.0)
        window_id = self.host.context.window.as_pointer()
        self.manager.handle_event(window_id, event(x=125, y=130))
        message = self.renderer.input.call_args.args[2]
        self.assertEqual((message["x"], message["y"]), (12.5, 24.5))
        self.host.context.preferences.system.ui_scale = 1.0
        self.manager.tick()
        self.assertEqual((surface.width, surface.height, surface.resize_revision), (100, 80, 1))
        self.assertEqual(self.manager.get_info(surface_id)["ui_scale"], 1.0)
        self.renderer.resize.assert_called_once_with(
            surface_id, surface.generation, width=100, height=80
        )

    def test_missing_and_invalid_ui_scale_fall_back_to_one(self):
        self.assertEqual(self.manager._ui_scale(), 1.0)
        system = SimpleNamespace(ui_scale=None)
        self.host.context.preferences = SimpleNamespace(system=system)
        for value in (None, "invalid", 0, -1, float("nan"), float("inf")):
            system.ui_scale = value
            self.assertEqual(self.manager._ui_scale(), 1.0)

    def test_input_diagnostics_are_bounded_and_never_retain_committed_text(self):
        surface_id = self.open()
        window_id = self.host.context.window.as_pointer()
        self.manager.handle_event(window_id, event(x=125, y=130, unicode="private text"))
        info = self.manager.get_info(surface_id)
        self.assertEqual(info["input_events"], 1)
        self.assertEqual(
            info["last_input"],
            {
                "type": "LEFTMOUSE",
                "value": "PRESS",
                "inside": True,
                "x": 25.0,
                "y": 49.0,
                "handled": True,
            },
        )
        info["last_input"]["type"] = "external mutation"
        self.assertEqual(self.manager.get_info(surface_id)["last_input"]["type"], "LEFTMOUSE")
        self.manager.handle_event(window_id, event("LEFTMOUSE", "RELEASE", x=0, y=0))
        info = self.manager.get_info(surface_id)
        self.assertEqual(info["input_events"], 2)
        self.assertFalse(info["last_input"]["inside"])
        self.assertNotIn("private text", str(info))
        self.assertEqual(len(info["last_input"]), 6)

    def test_draw_callback_is_scoped_to_exact_window_area_region_and_space(self):
        self.open()
        with patch.object(self.manager, "_draw_surface") as draw:
            self.manager._draw()
            draw.assert_called_once()
            draw.reset_mock()
            self.host.context.region = self.host.context.area.regions[1]
            self.manager._draw()
            draw.assert_not_called()

    def test_draw_uses_flipped_quad_straight_alpha_and_restores_blend_after_failure(self):
        surface_id = self.open()
        surface = self.manager.surfaces[surface_id]
        surface.frame = self.frame(surface_id)
        surface.sequence = surface.uploaded_sequence = 1
        surface.texture = object()
        shader = SimpleNamespace(bind=Mock(), uniform_sampler=Mock())
        batch = SimpleNamespace(draw=Mock(side_effect=RuntimeError("draw failed")))
        make_batch = Mock(return_value=batch)
        gpu = SimpleNamespace(
            shader=SimpleNamespace(from_builtin=Mock(return_value=shader)),
            state=SimpleNamespace(blend_get=Mock(return_value="NONE"), blend_set=Mock()),
        )
        modules = {
            "gpu": gpu,
            "numpy": SimpleNamespace(),
            "gpu_extras": SimpleNamespace(),
            "gpu_extras.batch": SimpleNamespace(batch_for_shader=make_batch),
        }
        with patch.dict(sys.modules, modules), self.assertRaisesRegex(RuntimeError, "draw failed"):
            self.manager._draw_surface(surface, self.host.context.region)
        self.assertEqual(make_batch.call_args.args[2]["texCoord"], surfaces.QUAD_UVS)
        self.assertEqual(
            [call.args[0] for call in gpu.state.blend_set.call_args_list], ["ALPHA", "NONE"]
        )
        shader.uniform_sampler.assert_called_once_with("image", surface.texture)

    def test_expired_surface_messages_do_not_reach_host_callback(self):
        surface_id = self.open()
        generation = self.manager.surfaces[surface_id].generation
        self.renderer.messages = [
            dict(type="call", surface_id=surface_id, generation=generation + 1),
            dict(type="error", surface_id="closed", generation=generation),
            dict(type="error", message="connection failed"),
        ]
        self.manager.tick()
        self.assertEqual(self.messages, [dict(type="error", message="connection failed")])
        self.assertEqual(self.manager.last_error, "connection failed")

    def test_input_uses_top_left_coordinates_and_passes_borders_header_and_sidebar(self):
        surface_id = self.open()
        window = self.host.context.window.as_pointer()
        self.assertFalse(self.manager.handle_event(window, event(x=101)))
        self.assertFalse(self.manager.handle_event(window, event(y=185)))
        self.host.context.area.regions.append(Pointer(type="UI", x=180, y=100, width=20, height=80))
        self.assertFalse(self.manager.handle_event(window, event(x=190)))
        self.renderer.input.reset_mock()
        self.assertTrue(self.manager.handle_event(window, event(x=125, y=130)))
        message = self.renderer.input.call_args.args[2]
        self.assertEqual(message["type"], "mouseDown")
        self.assertEqual((message["x"], message["y"]), (25, 49))
        self.assertEqual(
            self.renderer.input.call_args.args[:2],
            (surface_id, self.manager.surfaces[surface_id].generation),
        )
        self.assertTrue(self.manager.handle_event(window, event(value="RELEASE", x=99)))

    def test_keyboard_focus_moves_between_surfaces_and_deactivate_releases(self):
        first = self.open()
        self.second_area()
        second = self.open()
        window = self.host.context.window.as_pointer()
        self.manager.handle_event(window, event(x=120))
        self.manager.handle_event(window, event(value="RELEASE", x=120))
        self.manager.handle_event(window, event(x=220))
        self.assertFalse(self.manager.surfaces[first].input_state.focused)
        self.assertTrue(self.manager.surfaces[second].input_state.focused)
        self.renderer.input.reset_mock()
        self.assertTrue(self.manager.handle_event(window, event("A", unicode="a", x=220)))
        self.assertTrue(all(call.args[0] == second for call in self.renderer.input.call_args_list))
        self.assertFalse(self.manager.handle_event(window, event("WINDOW_DEACTIVATE")))
        self.assertFalse(self.manager.surfaces[second].input_state.focused)

    def test_dead_process_unmounts_all_surfaces_and_modal_ownership(self):
        self.open()
        self.second_area()
        self.open()
        self.renderer.alive = False
        self.assertFalse(self.manager.tick())
        self.assertFalse(self.manager.surfaces)
        self.assertFalse(self.manager._modal_windows)
        self.assertEqual(self.manager.last_error, "Offscreen renderer exited")
        self.host.bpy.types.SpaceImageEditor.draw_handler_remove.assert_called_once()

    def test_dead_parent_is_retained_until_process_tree_cleanup_is_complete(self):
        self.open()
        self.renderer.alive = False
        self.renderer.poll = Mock(return_value=[])
        self.assertTrue(self.manager.tick())
        self.assertFalse(self.manager.surfaces)
        self.assertIsNone(self.manager.renderer)
        self.assertEqual(self.manager._closing, [self.renderer])
        self.renderer.shutdown.assert_called_once()
        self.renderer.closed = True
        self.assertFalse(self.manager.tick())

    def test_failed_dead_parent_cleanup_retains_owner_and_retries_boundedly(self):
        self.open()
        self.renderer.alive = False
        self.renderer.poll = Mock(side_effect=RuntimeError("descendant not reaped"))
        self.renderer.terminate.side_effect = RuntimeError("Job cleanup failed")
        with self.assertLogs("auroraview_blender.surfaces", level="WARNING"):
            self.assertTrue(self.manager.tick())
        self.assertEqual(self.manager._closing, [self.renderer])
        self.renderer.terminate.assert_called_once()
        self.renderer.terminate.side_effect = self.renderer._terminate
        with self.assertLogs("auroraview_blender.surfaces", level="WARNING"):
            self.assertFalse(self.manager.tick())
        self.assertEqual(self.renderer.terminate.call_count, 2)
        self.assertTrue(self.renderer.closed)
        self.assertFalse(self.manager._closing)

    def test_force_stop_retains_process_tree_until_closed_is_confirmed(self):
        self.open()
        self.renderer.terminate.side_effect = lambda: None
        self.manager.stop(force=True)
        self.assertTrue(self.manager.needs_tick)
        self.assertEqual(self.manager._closing, [self.renderer])
        self.assertFalse(self.manager.tick())
        self.renderer.terminate.side_effect = self.renderer._terminate

    def test_handler_failure_retains_ownership_and_retries_on_tick(self):
        surface_id = self.open()
        self.host.bpy.types.SpaceImageEditor.draw_handler_remove.side_effect = [
            RuntimeError("busy"),
            None,
        ]
        with self.assertLogs("auroraview_blender.surfaces", level="WARNING"):
            self.manager.close(surface_id)
        self.assertIsNotNone(self.manager._draw_handler)
        self.assertTrue(self.manager.needs_tick)
        self.assertFalse(self.manager.tick())
        self.assertIsNone(self.manager._draw_handler)
        self.assertEqual(self.host.bpy.types.SpaceImageEditor.draw_handler_remove.call_count, 2)

    def test_invalidated_rna_during_close_still_detaches_and_reaps_renderer(self):
        surface_id = self.open()
        with (
            patch.object(self.manager, "_lookup", side_effect=ReferenceError("removed RNA")),
            self.assertLogs("auroraview_blender.surfaces", level="WARNING"),
        ):
            self.manager.close(surface_id)
        self.assertFalse(self.manager.surfaces)
        self.assertIsNone(self.manager.renderer)
        self.renderer.shutdown.assert_called_once()
        self.host.bpy.types.SpaceImageEditor.draw_handler_remove.assert_called_once()
        self.assertFalse(self.manager.tick())

    def test_force_stop_terminates_owned_process_despite_native_and_transport_errors(self):
        first = self.open()
        self.second_area()
        self.open()
        self.manager.surfaces[first].input_state.focused = True
        self.renderer.input.side_effect = BrokenPipeError("input failed")
        self.renderer.close_surface.side_effect = BrokenPipeError("close failed")
        self.renderer.shutdown.side_effect = BrokenPipeError("shutdown failed")
        self.host.bpy.types.SpaceImageEditor.draw_handler_remove.side_effect = RuntimeError(
            "native busy"
        )
        with self.assertLogs("auroraview_blender.surfaces", level="WARNING"):
            self.manager.stop(force=True)
        self.assertFalse(self.manager.surfaces)
        self.assertIsNone(self.manager.renderer)
        self.assertFalse(self.renderer.alive)
        self.renderer.terminate.assert_called()
        self.assertEqual(self.renderer.close_surface.call_count, 2)
        self.assertIsNotNone(self.manager._draw_handler)
        self.host.bpy.types.SpaceImageEditor.draw_handler_remove.side_effect = None
        self.manager.stop(force=True)
        self.assertFalse(self.manager.needs_tick)

    def test_new_surface_keeps_old_process_pumping_and_force_stop_owns_both(self):
        first = self.open()
        self.manager.close(first)
        second_renderer = Renderer()
        self.manager._factory = lambda: second_renderer
        self.open()
        self.assertIs(self.manager.renderer, second_renderer)
        self.assertTrue(self.renderer.alive)
        self.manager.stop(force=True)
        self.renderer.terminate.assert_called_once()
        second_renderer.terminate.assert_called_once()
        self.assertFalse(self.manager.needs_tick)

    def test_poll_failure_and_callback_failure_do_not_leave_owned_host_resources(self):
        surface_id = self.open()
        self.manager._on_message = Mock(side_effect=ValueError("callback failed"))
        self.renderer.messages = [
            dict(
                type="ready",
                surface_id=surface_id,
                generation=self.manager.surfaces[surface_id].generation,
            )
        ]
        with self.assertLogs("auroraview_blender.surfaces", level="WARNING"):
            self.manager.tick()
        self.assertTrue(self.manager.surfaces)
        self.renderer.poll = Mock(side_effect=BrokenPipeError("poll failed"))
        with self.assertLogs("auroraview_blender.surfaces", level="WARNING"):
            self.manager.tick()
        self.assertFalse(self.manager.surfaces)
        self.renderer.terminate.assert_called_once()

    def test_open_rolls_back_if_modal_operator_or_renderer_create_fails(self):
        self.host.bpy.ops.auroraview.surface_input.return_value = {"CANCELLED"}
        with self.assertRaisesRegex(RuntimeError, "did not start"):
            self.open()
        self.assertEqual(self.host.context.area.type, "VIEW_3D")
        self.assertFalse(self.manager.surfaces)
        self.assertIsNone(self.manager._draw_handler)
        self.assertTrue(self.manager.needs_tick)

    def test_modal_operator_multiplexes_owned_window_and_passes_native_input(self):
        self.open()
        classes = operator_classes(lambda: self.manager, bpy=self.host.bpy)
        self.assertEqual(len(classes), 1)
        operator = classes[0]()
        self.assertEqual(operator.bl_idname, "auroraview.surface_input")
        self.assertEqual(operator.invoke(self.host.context, event()), {"RUNNING_MODAL"})
        self.assertEqual(operator.modal(self.host.context, event(x=101)), {"PASS_THROUGH"})
        self.assertEqual(operator.modal(self.host.context, event()), {"RUNNING_MODAL"})
        self.manager.stop(force=True)
        self.assertEqual(operator.modal(self.host.context, event()), {"CANCELLED"})

    def test_bpy_entry_points_refuse_worker_thread_before_host_access(self):
        callbacks = [
            self.open,
            self.manager.tick,
            lambda: self.manager.close("missing"),
            self.manager.stop,
            self.manager._draw,
            lambda: self.manager.handle_event(1, event()),
        ]
        errors = []

        def run():
            for callback in callbacks:
                try:
                    callback()
                except Exception as exc:
                    errors.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), len(callbacks))
        self.assertTrue(all(isinstance(error, RuntimeError) for error in errors))
        self.assertFalse(self.manager.surfaces)
        self.renderer.create.assert_not_called()


class SurfaceCapabilityTests(unittest.TestCase):
    def test_windows_nonblocking_pipe_minimum_and_other_platforms_are_explicit(self):
        self.assertFalse(surface_capabilities("win32", (3, 11))["native_offscreen_surface"])
        self.assertTrue(surface_capabilities("win32", (3, 12))["native_offscreen_surface"])
        self.assertFalse(surface_capabilities("linux", (3, 13))["native_offscreen_surface"])
        self.assertFalse(surface_capabilities("win32", (3, 13))["verified"])

    def test_renderer_dimension_and_pixel_limits_apply_to_ultrawide_and_large_regions(self):
        for width, height in ((7000, 300), (5000, 5000), (100, 100), (0, 0)):
            result = surfaces._size(SimpleNamespace(width=width, height=height))
            self.assertLessEqual(max(result), surfaces.MAX_DIMENSION)
            self.assertLessEqual(result[0] * result[1], surfaces.MAX_PIXELS)
            self.assertGreaterEqual(min(result), 1)
        self.assertEqual(surfaces._size(SimpleNamespace(width=7000, height=300), 2.0), (3500, 150))
        self.assertEqual(surfaces.QUAD_UVS, ((0, 1), (1, 1), (1, 0), (0, 0)))


if __name__ == "__main__":
    unittest.main()
