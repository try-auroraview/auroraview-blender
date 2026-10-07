import importlib
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import auroraview_blender as addon
from auroraview_blender.runtime import BlenderScheduler, BlenderSession, capabilities


class Timers:
    def __init__(self):
        self.registered = {}
        self.owner = threading.get_ident()

    def register(self, callback, **options):
        assert threading.get_ident() == self.owner
        self.registered[callback] = options

    def unregister(self, callback):
        assert threading.get_ident() == self.owner
        del self.registered[callback]

    def is_registered(self, callback):
        assert threading.get_ident() == self.owner
        return callback in self.registered

    def tick(self):
        for callback in list(self.registered):
            if callback() is None:
                self.registered.pop(callback, None)


def fake_bpy():
    classes = []
    return SimpleNamespace(
        app=SimpleNamespace(
            background=False,
            version_string="test-host",
            timers=Timers(),
            handlers=SimpleNamespace(load_pre=[], load_post=[], persistent=lambda f: f),
        ),
        context=SimpleNamespace(selected_objects=[SimpleNamespace(name="Cube")]),
        types=SimpleNamespace(Operator=type("Operator", (), {}), Panel=type("Panel", (), {})),
        utils=SimpleNamespace(register_class=classes.append, unregister_class=classes.remove),
        classes=classes,
    )


class View:
    def __init__(self, **options):
        self.options = options
        self.alive = False
        self.methods = {}
        self.close_count = 0

    def set_call_dispatcher(self, dispatcher):
        self.dispatch = dispatcher

    def bind_call(self, name, callback):
        self.methods[name] = callback

    def show(self, *, wait):
        assert wait is False
        self.alive = True

    def is_alive(self):
        return self.alive

    def request_close(self):
        self.close_count += 1
        self.alive = False

    close = request_close


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.bpy = fake_bpy()
        self.scheduler = BlenderScheduler(self.bpy, batch_size=2)

    def tearDown(self):
        self.scheduler.stop()

    def test_one_timer_and_idempotent_start_stop(self):
        self.scheduler.start()
        self.scheduler.start()
        self.assertEqual(len(self.bpy.app.timers.registered), 1)
        self.scheduler.stop()
        self.scheduler.stop()
        self.assertEqual(len(self.bpy.app.timers.registered), 0)

    def test_worker_submission_never_touches_bpy(self):
        self.scheduler.start()
        called = []
        worker = threading.Thread(
            target=lambda: self.scheduler.submit(lambda: called.append(threading.get_ident()))
        )
        worker.start()
        worker.join()
        self.assertEqual(called, [])
        self.bpy.app.timers.tick()
        self.assertEqual(called, [threading.get_ident()])

    def test_bounded_work_per_tick(self):
        self.scheduler.start()
        called = []
        for index in range(3):
            self.scheduler.submit(lambda i=index: called.append(i))
        self.bpy.app.timers.tick()
        self.assertEqual(called, [0, 1])
        self.assertEqual(self.scheduler.pending, 1)
        self.bpy.app.timers.tick()
        self.assertEqual(called, [0, 1, 2])

    def test_stop_discards_pending_work(self):
        self.scheduler.start()
        called = []
        self.scheduler.submit(lambda: called.append(True))
        self.scheduler.stop()
        self.assertEqual(self.scheduler.pending, 0)
        self.scheduler.start()
        self.bpy.app.timers.tick()
        self.assertEqual(called, [])

    def test_closed_and_full_queue_reject(self):
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.scheduler.submit(lambda: None)
        self.scheduler.start()
        for _ in range(1024):
            self.scheduler.submit(lambda: None)
        with self.assertRaisesRegex(RuntimeError, "full"):
            self.scheduler.submit(lambda: None)

    def test_stop_inside_callback_stops_batch(self):
        self.scheduler.start()
        called = []
        self.scheduler.submit(self.scheduler.stop)
        self.scheduler.submit(lambda: called.append(True))
        self.bpy.app.timers.tick()
        self.assertEqual(called, [])
        self.assertFalse(self.scheduler.running)

    def test_exception_does_not_kill_timer(self):
        self.scheduler.start()
        self.scheduler.submit(lambda: 1 / 0)
        called = []
        self.scheduler.submit(lambda: called.append(True))
        with self.assertLogs("auroraview_blender.runtime", level="ERROR"):
            self.bpy.app.timers.tick()
        self.assertEqual(called, [True])
        self.assertIn("zero", self.scheduler.last_error)

    def test_background_rejected(self):
        self.bpy.app.background = True
        with self.assertRaisesRegex(RuntimeError, "interactive"):
            self.scheduler.start()


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.bpy = fake_bpy()
        self.session = BlenderSession(self.bpy, view_factory=View)
        self.session.start()

    def tearDown(self):
        self.session.stop()

    def test_unvalidated_platform_fails_before_view_creation(self):
        for platform in ["linux", "darwin"]:
            with patch("auroraview_blender.runtime.sys.platform", platform):
                with self.assertRaisesRegex(RuntimeError, "not validated"):
                    self.session.open()
        self.assertEqual(self.session.views, {})

    def test_context_on_host_thread(self):
        self.assertEqual(self.session.context()["selected_objects"], ["Cube"])

    @patch("auroraview_blender.runtime.sys.platform", "win32")
    def test_missing_core_contract_rejects_before_construction(self):
        class OldCore:
            def __init__(self, **options):
                raise AssertionError("An incompatible Core must not be constructed")

        self.session._view_factory = None
        with patch.dict(sys.modules, {"auroraview": SimpleNamespace(WebView=OldCore)}):
            with self.assertRaisesRegex(RuntimeError, "host lifecycle contract"):
                self.session.open()
        self.assertEqual(self.session.views, {})

    @patch("auroraview_blender.runtime.sys.platform", "win32")
    def test_core_contract_and_two_independent_views(self):
        first = self.session.open("first")
        second = self.session.open("second")
        self.assertTrue(first.options["dcc_mode"])
        self.assertIs(self.session.open("first"), first)
        self.assertIn("blender.context", first.methods)
        result = []
        first.dispatch(lambda: result.append(first.methods["blender.context"]()))
        self.bpy.app.timers.tick()
        self.assertTrue(result[0]["main_thread"])
        self.session.close("first")
        self.assertEqual(first.close_count, 1)
        self.assertTrue(second.alive)
        self.assertIsNot(self.session.open("first"), first)

    @patch("auroraview_blender.runtime.sys.platform", "win32")
    def test_failed_close_keeps_reference_for_retry(self):
        first = self.session.open()
        with patch.object(first, "request_close", side_effect=RuntimeError("failed")):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                self.session.close("main")
        self.assertIs(self.session.views["main"], first)

    def test_capabilities_do_not_claim_native_embedding_or_verification(self):
        for platform in ["linux", "win32", "darwin"]:
            report = capabilities(platform)
            self.assertFalse(report["native_panel_embedding"])
            self.assertFalse(report["window_route_verified"])


class AddonTests(unittest.TestCase):
    def test_module_reload_disposes_previous_generation(self):
        bpy = fake_bpy()
        with patch.dict(sys.modules, {"bpy": bpy}):
            addon.register()
            old_session = addon._session
            importlib.reload(addon)
            self.assertFalse(old_session.scheduler.running)
            self.assertIsNone(addon._session)
            self.assertEqual(bpy.classes, [])
            self.assertEqual(bpy.app.handlers.load_pre, [])
            self.assertEqual(bpy.app.handlers.load_post, [])

    def test_exit_callback_stops_host_resources(self):
        bpy = fake_bpy()
        with patch.dict(sys.modules, {"bpy": bpy}):
            try:
                addon.register()
                addon._exit_callback()
                self.assertFalse(addon._session.scheduler.running)
                self.assertEqual(len(bpy.app.timers.registered), 0)
            finally:
                addon.unregister()
            self.assertIsNone(addon._exit_callback)

    def test_register_unregister_and_file_reload(self):
        bpy = fake_bpy()
        with patch.dict(sys.modules, {"bpy": bpy}):
            try:
                addon.register()
                addon.register()
                self.assertEqual(len(bpy.classes), 2)
                self.assertEqual(len(bpy.app.handlers.load_pre), 1)
                self.assertEqual(len(bpy.app.handlers.load_post), 1)
                self.assertEqual(len(bpy.app.timers.registered), 1)
                bpy.app.handlers.load_pre[0](None)
                self.assertEqual(len(bpy.app.timers.registered), 0)
                bpy.app.handlers.load_post[0](None)
                self.assertEqual(len(bpy.app.timers.registered), 1)
            finally:
                addon.unregister()
                addon.unregister()
            self.assertEqual(bpy.classes, [])
            self.assertEqual(bpy.app.handlers.load_pre, [])
            self.assertEqual(bpy.app.handlers.load_post, [])
            self.assertEqual(len(bpy.app.timers.registered), 0)


if __name__ == "__main__":
    unittest.main()
