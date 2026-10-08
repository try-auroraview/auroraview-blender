"""Registration epochs and retryable native host cleanup."""

import atexit
import sys
import threading
import unittest
from unittest.mock import patch

from test_runtime import View, fake_bpy

import auroraview_blender as addon
from auroraview_blender import BlenderSession


class LifecycleTests(unittest.TestCase):
    def test_public_host_exit_hook_is_persistent_and_removed_on_disable(self):
        bpy = fake_bpy()
        bpy.app.handlers.exit_pre = []

        def persistent(callback):
            callback._bpy_persistent = True
            return callback

        bpy.app.handlers.persistent = persistent
        with patch.dict(sys.modules, {"bpy": bpy}):
            with patch("auroraview_blender.atexit.register") as register_atexit:
                addon.register()
            try:
                hook = bpy.app.handlers.exit_pre[0]
                self.assertTrue(hook._bpy_persistent)
                self.assertIsNone(addon._exit_callback)
                register_atexit.assert_not_called()
                bpy.app.handlers.load_pre[0](None)
                bpy.app.handlers.load_post[0](None)
                self.assertEqual(bpy.app.handlers.exit_pre, [hook])
            finally:
                addon.unregister()
            self.assertEqual(bpy.app.handlers.exit_pre, [])

    def test_public_host_exit_releases_resources_without_skipping_other_handlers(self):
        bpy = fake_bpy()
        bpy.app.handlers.exit_pre = []
        with patch.dict(sys.modules, {"bpy": bpy}):
            addon.register()
            session = addon._session
            following = []
            bpy.app.handlers.exit_pre.append(lambda _flag: following.append(addon._session))
            # Match Blender's live list iteration rather than iterating a copy.
            for callback in bpy.app.handlers.exit_pre:
                callback(True)
            self.assertEqual(following, [None])
            self.assertFalse(session.scheduler.running)
            self.assertEqual(bpy.classes, [])
            self.assertFalse(addon._handlers)
            self.assertIsNone(addon._session)

    def test_public_host_exit_rejects_worker_before_changing_owned_handlers(self):
        bpy = fake_bpy()
        bpy.app.handlers.exit_pre = []
        with patch.dict(sys.modules, {"bpy": bpy}):
            addon.register()
            hook = bpy.app.handlers.exit_pre[0]
            errors = []

            def worker():
                try:
                    hook(True)
                except RuntimeError as exc:
                    errors.append(str(exc))

            try:
                thread = threading.Thread(target=worker)
                thread.start()
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(len(errors), 1)
                self.assertIn("main thread", errors[0])
                self.assertTrue(addon._session.scheduler.running)
                self.assertIn((bpy.app.handlers.exit_pre, hook), addon._handlers)
            finally:
                addon.unregister()

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
                self.assertEqual(len(bpy.classes), 7)
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

    def test_exit_cleanup_failure_retains_native_class_for_public_retry(self):
        bpy = fake_bpy()
        with patch.dict(sys.modules, {"bpy": bpy}):
            addon.register()
            session, exit_callback = addon._session, addon._exit_callback
            rejected = bpy.classes[0]
            remove = bpy.utils.unregister_class

            def unregister(cls):
                if cls is rejected:
                    raise RuntimeError("host busy")
                remove(cls)

            try:
                with patch.object(bpy.utils, "unregister_class", unregister):
                    with self.assertLogs("auroraview_blender", level="ERROR"):
                        exit_callback()
                self.assertIs(addon._session, session)
                self.assertIs(addon._exit_callback, exit_callback)
                self.assertFalse(session.scheduler.running)
                self.assertEqual(bpy.classes, [rejected])
                self.assertEqual(bpy.app.handlers.load_pre, [])
                self.assertEqual(bpy.app.handlers.load_post, [])
                exit_callback()
                self.assertIsNone(addon._session)
                self.assertIsNone(addon._exit_callback)
                self.assertEqual(bpy.classes, [])
            finally:
                addon.unregister()

    def test_interpreter_exit_cleanup_does_not_mutate_atexit_callbacks(self):
        bpy = fake_bpy()
        with patch.dict(sys.modules, {"bpy": bpy}):
            addon.register()
            exit_callback = addon._exit_callback
            try:
                with patch("auroraview_blender.atexit.unregister") as remove_callback:
                    exit_callback(finalizing=True)
                    remove_callback.assert_not_called()
                self.assertIsNone(addon._session)
                self.assertIsNone(addon._exit_callback)
                self.assertFalse(addon._finalizing)
                self.assertEqual(bpy.classes, [])
            finally:
                atexit.unregister(exit_callback)
                addon.unregister()

    def test_file_load_cleanup_failure_stops_old_generation_before_public_retry(self):
        bpy = fake_bpy()
        with patch.dict(sys.modules, {"bpy": bpy}):
            addon.register()
            docking, scheduler = addon._docking, addon._session.scheduler
            old_dispatch = scheduler.dispatcher()
            delivered = []
            old_dispatch(lambda: delivered.append("old"))
            retained = object()
            docking.manager = retained
            attempts = []

            def stop(*, force):
                self.assertTrue(force)
                attempts.append(scheduler.running)
                if len(attempts) == 1:
                    raise RuntimeError("cleanup pending")
                docking.manager = None

            try:
                with patch.object(docking, "stop", side_effect=stop):
                    with self.assertRaisesRegex(RuntimeError, "cleanup pending"):
                        bpy.app.handlers.load_pre[0](None)
                    self.assertFalse(scheduler.running)
                    self.assertEqual(scheduler.pending, 0)
                    self.assertEqual(len(bpy.app.timers.registered), 0)
                    self.assertIs(docking.manager, retained)
                    bpy.app.handlers.load_post[0](None)
                self.assertEqual(attempts, [True, False])
                self.assertTrue(scheduler.running)
                with self.assertRaisesRegex(RuntimeError, "expired"):
                    old_dispatch(lambda: delivered.append("stale"))
                bpy.app.timers.tick()
                self.assertEqual(delivered, [])
            finally:
                docking.manager = None
                addon.unregister()

    def test_failed_file_load_retry_never_restarts_host_scheduler(self):
        bpy = fake_bpy()
        with patch.dict(sys.modules, {"bpy": bpy}):
            addon.register()
            docking, scheduler = addon._docking, addon._session.scheduler
            retained = object()
            docking.manager = retained
            try:
                with patch.object(docking, "stop", side_effect=RuntimeError("cleanup pending")):
                    with self.assertRaisesRegex(RuntimeError, "cleanup pending"):
                        bpy.app.handlers.load_pre[0](None)
                    with self.assertRaisesRegex(RuntimeError, "cleanup pending"):
                        bpy.app.handlers.load_post[0](None)
                self.assertFalse(scheduler.running)
                self.assertEqual(len(bpy.app.timers.registered), 0)
                self.assertIs(docking.manager, retained)
            finally:
                docking.manager = None
                addon.unregister()


if __name__ == "__main__":
    unittest.main()
