"""Public configuration contract with explicit host/Core doubles, not native QA."""

import gc
import threading
import unittest
import weakref
from unittest.mock import patch

from test_runtime import View, fake_bpy

from auroraview_blender import BlenderSession


class ConfigurableView(View):
    def __init__(self, **options):
        super().__init__(**options)
        self.steps = []
        self.events = {}

    def set_call_dispatcher(self, dispatcher):
        self.steps.append("dispatcher")
        super().set_call_dispatcher(dispatcher)

    def set_event_dispatcher(self, dispatcher):
        self.steps.append("event_dispatcher")
        super().set_event_dispatcher(dispatcher)

    def bind_call(self, name, callback, *, allow_rebind=True):
        assert not self.alive, "Bindings must precede native ownership transfer"
        self.steps.append(name)
        if allow_rebind or name not in self.methods:
            super().bind_call(name, callback)

    def on(self, name, callback):
        assert not self.alive, "Events must precede native ownership transfer"
        self.steps.append(name)
        self.events[name] = callback
        return len(self.events)

    def show(self, *, wait):
        self.steps.append("show")
        super().show(wait=wait)

    def request_close(self):
        self.steps.append("close")
        super().request_close()


@patch("auroraview_blender.runtime.sys.platform", "win32")
class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.bpy = fake_bpy()
        self.session = BlenderSession(self.bpy, view_factory=ConfigurableView)
        self.session.start()

    def tearDown(self):
        self.session.stop()

    def test_configure_commands_and_events_before_show_on_main_thread(self):
        def configure(view):
            self.assertIs(threading.current_thread(), threading.main_thread())
            self.assertTrue(callable(view.dispatch))
            self.assertIs(view.event_dispatch, view.dispatch)
            self.assertEqual(view.steps, ["dispatcher", "event_dispatcher", "blender.context"])
            view.bind_call(
                "org.example.inspector.selection", self.session.context, allow_rebind=False
            )
            view.on("org.example.inspector.ready", lambda _data: None)

        view = self.session.open("org.example.inspector", configure=configure, html="own HTML")
        self.assertEqual(
            view.steps,
            [
                "dispatcher",
                "event_dispatcher",
                "blender.context",
                "org.example.inspector.selection",
                "org.example.inspector.ready",
                "show",
            ],
        )
        self.assertNotIn("configure", view.options)
        self.assertEqual(view.options["html"], "own HTML")
        called = []
        worker = threading.Thread(
            target=lambda: view.dispatch(
                lambda: called.append(view.methods["org.example.inspector.selection"]())
            )
        )
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(called, [])
        self.bpy.app.timers.tick()
        self.assertEqual(called[0]["selected_objects"], ["Cube"])
        self.assertTrue(called[0]["main_thread"])

    def test_events_use_main_thread_queue_and_expire_on_file_load(self):
        view = self.session.open()
        called = []
        worker = threading.Thread(
            target=lambda: view.event_dispatch(lambda: called.append(self.session.context()))
        )
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(called, [])
        self.bpy.app.timers.tick()
        self.assertTrue(called[0]["main_thread"])
        view.event_dispatch(lambda: called.append("stale"))
        self.session.stop()
        self.session.start()
        self.bpy.app.timers.tick()
        self.assertEqual(len(called), 1)
        with self.assertRaisesRegex(RuntimeError, "expired session"):
            view.event_dispatch(lambda: called.append("stale"))

    def test_invalid_configure_rejected_before_construction_and_on_live_reuse(self):
        for invalid in (False, 42, "configure", object()):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(TypeError, "configure must be callable or None"):
                    self.session.open(configure=invalid)
        self.assertEqual(self.session.views, {})
        first = self.session.open()
        with self.assertRaisesRegex(TypeError, "configure must be callable or None"):
            self.session.open(configure=42)
        self.assertIs(self.session.views["main"], first)
        self.assertTrue(first.alive)

    def test_live_reuse_does_not_reconfigure_or_apply_new_options(self):
        configured = []
        first = self.session.open("org.example.tool", configure=configured.append, title="First")
        second = self.session.open(
            "org.example.tool",
            configure=lambda _view: self.fail("must not reconfigure"),
            title="Next",
        )
        self.assertIs(first, second)
        self.assertEqual(configured, [first])
        self.assertEqual(first.options["title"], "First")
        self.assertEqual(first.steps.count("show"), 1)

    def test_close_reopen_requires_configuration_again(self):
        configured = []
        first = self.session.open("org.example.tool", configure=configured.append)
        self.session.close("org.example.tool")
        second = self.session.open("org.example.tool", configure=configured.append)
        self.assertIsNot(first, second)
        self.assertEqual(configured, [first, second])
        self.session.close("org.example.tool")
        third = self.session.open("org.example.tool")
        self.assertEqual(configured, [first, second])
        self.assertEqual(third.steps, ["dispatcher", "event_dispatcher", "blender.context", "show"])

    def test_dead_view_replacement_configures_fresh_view(self):
        configured = []
        first = self.session.open("org.example.tool", configure=configured.append)
        first.alive = False
        second = self.session.open("org.example.tool", configure=configured.append)
        self.assertEqual(first.close_count, 1)
        self.assertIsNot(first, second)
        self.assertEqual(configured, [first, second])

    def test_callback_object_is_not_retained_by_session(self):
        class Configure:
            def __call__(self, view):
                view.bind_call("org.example.tool.read", lambda: "value")

        configure = Configure()
        reference = weakref.ref(configure)
        self.session.open(configure=configure)
        del configure
        gc.collect()
        self.assertIsNone(reference())

    def test_configure_failure_closes_unshown_view_and_preserves_other_consumer(self):
        other = self.session.open("org.other.tool")
        failed = []

        def configure(view):
            failed.append(view)
            view.bind_call("org.example.tool.read", lambda: "value")
            raise ValueError("configuration failed")

        with self.assertRaisesRegex(ValueError, "configuration failed"):
            self.session.open("org.example.tool", configure=configure)
        self.assertEqual(failed[0].close_count, 1)
        self.assertNotIn("show", failed[0].steps)
        self.assertNotIn("org.example.tool", self.session.views)
        self.assertIs(self.session.views["org.other.tool"], other)
        self.assertTrue(other.alive)
        self.assertTrue(self.session.scheduler.running)
        self.assertEqual(self.session.scheduler.pending, 0)
        self.assertEqual(len(self.bpy.app.timers.registered), 1)
        reopened = self.session.open("org.example.tool", configure=lambda view: None)
        self.assertIsNot(reopened, failed[0])
        self.session.stop()
        self.assertEqual(self.session.views, {})
        self.assertEqual(len(self.bpy.app.timers.registered), 0)

    def test_configure_base_exception_also_rolls_back(self):
        created = []

        def configure(view):
            created.append(view)
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            self.session.open(configure=configure)
        self.assertEqual(created[0].close_count, 1)
        self.assertEqual(self.session.views, {})

    def test_failed_rollback_retains_view_for_explicit_close_retry(self):
        created = []

        def fail_close():
            raise RuntimeError("close failed")

        def configure(view):
            created.append(view)
            view.request_close = fail_close
            raise ValueError("configure failed")

        with self.assertRaisesRegex(RuntimeError, "close failed") as caught:
            self.session.open("org.example.tool", configure=configure)
        self.assertIsInstance(caught.exception.__context__, ValueError)
        self.assertIs(self.session.views["org.example.tool"], created[0])
        self.assertNotIn("show", created[0].steps)
        del created[0].request_close
        self.session.close("org.example.tool")
        self.assertEqual(created[0].close_count, 1)
        self.assertEqual(self.session.views, {})

    def test_show_failure_after_configuration_closes_new_view(self):
        created = []
        with patch.object(ConfigurableView, "show", side_effect=RuntimeError("show failed")):
            with self.assertRaisesRegex(RuntimeError, "show failed"):
                self.session.open(configure=created.append)
        self.assertEqual(created[0].close_count, 1)
        self.assertEqual(self.session.views, {})

    def test_worker_open_never_invokes_configure(self):
        errors = []
        configured = []

        def open_from_worker():
            try:
                self.session.open(configure=configured.append)
            except RuntimeError as exc:
                errors.append(str(exc))

        worker = threading.Thread(target=open_from_worker)
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(configured, [])
        self.assertEqual(errors, ["This Blender operation must run on the main thread"])
        self.assertEqual(self.session.views, {})
