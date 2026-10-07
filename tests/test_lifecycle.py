"""Registration epochs and retryable native host cleanup."""

import sys
import threading
import unittest
from unittest.mock import patch

from test_runtime import View, fake_bpy

import auroraview_blender as addon
from auroraview_blender import BlenderSession


class LifecycleTests(unittest.TestCase):
    @patch("auroraview_blender.runtime.sys.platform", "win32")
    def test_restart_retries_retained_view_before_accepting_new_work(self):
        bpy = fake_bpy()
        session = BlenderSession(bpy, view_factory=View)
        session.start()
        old = session.open()
        try:
            with patch.object(old, "request_close", side_effect=RuntimeError("host busy")):
                with self.assertRaisesRegex(RuntimeError, "Could not close"):
                    session.stop()
                with self.assertRaisesRegex(RuntimeError, "Could not close"):
                    session.start()
                self.assertFalse(session.scheduler.running)
                self.assertIs(session.views["main"], old)
            session.start()
            self.assertEqual(session.views, {})
            new = session.open()
            self.assertIsNot(new, old)
            called = []
            new.dispatch(lambda: called.append(True))
            bpy.app.timers.tick()
            self.assertEqual(called, [True])
        finally:
            session.stop()

    def test_timer_removal_failure_still_discards_old_work(self):
        bpy = fake_bpy()
        session = BlenderSession(bpy)
        session.start()
        called = []
        session.scheduler.submit(lambda: called.append("old"))
        with patch.object(bpy.app.timers, "unregister", side_effect=RuntimeError("host busy")):
            with self.assertRaisesRegex(RuntimeError, "host busy"):
                session.stop()
        self.assertEqual(session.scheduler.pending, 0)
        self.assertFalse(session.scheduler.running)
        session.stop()
        session.start()
        try:
            bpy.app.timers.tick()
            self.assertEqual(called, [])
        finally:
            session.stop()

    def test_old_dispatcher_cannot_cross_file_load_generation(self):
        bpy = fake_bpy()
        session = BlenderSession(bpy, view_factory=View)
        with patch("auroraview_blender.runtime.sys.platform", "win32"):
            session.start()
            old = session.open()
            called = []
            old.dispatch(lambda: called.append("old"))
            session.stop()
            session.start()
            try:
                with self.assertRaisesRegex(RuntimeError, "expired"):
                    old.dispatch(lambda: called.append("stale"))
                new = session.open()
                new.dispatch(lambda: called.append("new"))
                bpy.app.timers.tick()
                self.assertEqual(called, ["new"])
            finally:
                session.stop()

    def test_generation_dispatcher_rejects_worker_after_restart(self):
        bpy = fake_bpy()
        session = BlenderSession(bpy)
        session.start()
        dispatch = session.scheduler.dispatcher()
        session.stop()
        session.start()
        errors = []

        def worker():
            try:
                dispatch(lambda: self.fail("stale callback executed"))
            except RuntimeError as exc:
                errors.append(str(exc))

        try:
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIn("expired", errors[0])
            self.assertEqual(session.scheduler.pending, 0)
        finally:
            session.stop()

    def test_failed_class_cleanup_retains_owner_and_register_retries(self):
        bpy = fake_bpy()
        with patch.dict(sys.modules, {"bpy": bpy}):
            addon.register()
            previous = addon._session
            rejected = bpy.classes[0]
            remove = bpy.utils.unregister_class

            def unregister(cls):
                if cls is rejected:
                    raise RuntimeError("host busy")
                remove(cls)

            try:
                with patch.object(bpy.utils, "unregister_class", unregister):
                    with self.assertRaisesRegex(RuntimeError, "incomplete"):
                        addon.unregister()
                self.assertIs(addon._session, previous)
                self.assertEqual(bpy.classes, [rejected])
                self.assertFalse(previous.scheduler.running)
                addon.register()
                self.assertIsNot(addon._session, previous)
                self.assertEqual(len(bpy.classes), 3)
                self.assertEqual(len(bpy.app.handlers.load_pre), 1)
            finally:
                addon.unregister()
            self.assertEqual(bpy.classes, [])

    def test_public_native_panel_api_is_owned_by_addon(self):
        bpy = fake_bpy()
        with patch.dict(sys.modules, {"bpy": bpy}):
            with self.assertRaisesRegex(RuntimeError, "Register AuroraView"):
                addon.register_panel("EXAMPLE_PT_tool", "Tool", lambda layout, context: None)
            addon.register()
            try:
                panel = addon.register_panel(
                    "EXAMPLE_PT_tool", "Tool", lambda layout, context: None
                )
                self.assertIn(panel, bpy.classes)
                self.assertTrue(addon.unregister_panel("EXAMPLE_PT_tool"))
                self.assertFalse(addon.unregister_panel("EXAMPLE_PT_tool"))
                addon.register_panel("EXAMPLE_PT_tool", "Tool", lambda layout, context: None)
            finally:
                addon.unregister()
            self.assertEqual(bpy.classes, [])


if __name__ == "__main__":
    unittest.main()
