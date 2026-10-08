"""Borrow existing public backends without creating a server or scheduler."""

import inspect
import sys
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from auroraview_blender.docking import DockSession


class DockBackendTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = Mock(running=True)
        self.session = DockSession(SimpleNamespace(), self.scheduler)
        self.surface = SimpleNamespace(id="one", generation=1)
        self.renderer = Mock()
        self.manager = Mock(
            surfaces={"one": self.surface}, renderer=self.renderer, needs_tick=False
        )
        self.message = {
            "type": "call",
            "surface_id": "one",
            "generation": 1,
            "id": "request",
            "method": "existing.tool",
            "params": {"name": "Cube"},
        }

    def bind(self, backend, events=()):
        self.session._bind_backend(backend, events)
        self.session.manager = self.manager

    def test_existing_public_result_preserved_and_backend_never_stopped(self):
        result = {"content": [{"type": "text", "text": "ok"}], "isError": False}
        backend = Mock(call=Mock(return_value=result))
        self.bind(backend)
        self.session._message(self.message)
        backend.call.assert_called_once_with("existing.tool", {"name": "Cube"})
        self.renderer.call_result.assert_called_once_with("one", 1, "request", True, result=result)
        self.session.stop()
        backend.close.assert_not_called()
        backend.stop.assert_not_called()

    def test_owned_connection_removed_without_closing_borrowed_backend(self):
        connection = Mock(dispose=Mock(return_value=True))
        backend = Mock(spec=["call", "on", "close", "stop"], on=Mock(return_value=connection))
        self.bind(backend, ("existing.changed",))
        backend.on.call_args.args[1]({"name": "Cube"})
        self.renderer.emit.assert_called_once_with("one", 1, "existing.changed", {"name": "Cube"})
        self.session.stop()
        connection.dispose.assert_called_once()
        backend.close.assert_not_called()

    def test_public_subscribe_handle_completes_with_none_and_old_events_stay_invalid(self):
        remove = Mock(return_value=None)
        backend = Mock(
            spec=["call", "subscribe", "close", "stop"],
            subscribe=Mock(return_value=remove),
        )
        self.bind(backend, ("scene.changed",))
        old_callback = backend.subscribe.call_args.args[1]
        old_callback({"revision": 1})
        self.renderer.emit.assert_called_once_with("one", 1, "scene.changed", {"revision": 1})
        self.session.stop()
        remove.assert_called_once()
        self.assertFalse(self.session._connections)
        backend.close.assert_not_called()
        backend.stop.assert_not_called()

        self.bind(backend, ("scene.changed",))
        self.renderer.emit.reset_mock()
        old_callback({"revision": 2})
        self.renderer.emit.assert_not_called()
        backend.subscribe.call_args.args[1]({"revision": 3})
        self.renderer.emit.assert_called_once_with("one", 1, "scene.changed", {"revision": 3})

    def test_public_subscribe_preferred_over_legacy_on(self):
        remove = Mock(return_value=None)
        backend = Mock(spec=["call", "subscribe", "on"], subscribe=Mock(return_value=remove))
        self.bind(backend, ("changed",))
        self.session.stop()
        backend.subscribe.assert_called_once()
        backend.on.assert_not_called()
        remove.assert_called_once()

    def test_false_unsubscribe_retained_and_events_invalidated_before_retry(self):
        remove = Mock(side_effect=[False, None])
        backend = Mock(spec=["call", "subscribe", "close"], subscribe=Mock(return_value=remove))
        self.bind(backend, ("changed",))
        callback = backend.subscribe.call_args.args[1]
        with self.assertRaisesRegex(RuntimeError, "cleanup needs retry"):
            self.session.stop()
        self.assertIs(self.session.manager, self.manager)
        self.assertEqual(self.session._connections, [remove])
        callback({"revision": 1})
        self.renderer.emit.assert_not_called()
        self.session.stop()
        self.assertEqual(remove.call_count, 2)
        self.assertIsNone(self.session.manager)
        backend.close.assert_not_called()

    def test_public_unsubscribe_exception_retained_for_retry(self):
        remove = Mock(side_effect=[RuntimeError("busy"), None])
        backend = Mock(spec=["call", "subscribe"], subscribe=Mock(return_value=remove))
        self.bind(backend, ("changed",))
        with self.assertRaisesRegex(RuntimeError, "cleanup needs retry"):
            self.session.stop()
        self.assertEqual(self.session._connections, [remove])
        self.session.stop()
        self.assertFalse(self.session._connections)

    def test_awaitable_unsubscribe_closed_and_handle_retained_for_sync_retry(self):
        async def invalid_cleanup():
            return None

        coroutine = invalid_cleanup()
        remove = Mock(side_effect=[coroutine, None])
        backend = Mock(spec=["call", "subscribe"], subscribe=Mock(return_value=remove))
        self.bind(backend, ("changed",))
        with self.assertRaisesRegex(RuntimeError, "cleanup needs retry") as caught:
            self.session.stop()
        self.assertIsInstance(caught.exception.__cause__, TypeError)
        self.assertEqual(inspect.getcoroutinestate(coroutine), inspect.CORO_CLOSED)
        self.assertEqual(self.session._connections, [remove])
        self.session.stop()
        self.assertIsNone(self.session.manager)

    def test_awaitable_subscription_handle_closed_and_rejected(self):
        async def invalid_subscription():
            return lambda: None

        coroutine = invalid_subscription()
        backend = Mock(spec=["call", "subscribe"], subscribe=Mock(return_value=coroutine))
        with self.assertRaisesRegex(TypeError, "synchronous cleanup"):
            self.session._bind_backend(backend, ("changed",))
        self.assertEqual(inspect.getcoroutinestate(coroutine), inspect.CORO_CLOSED)
        self.assertIsNone(self.session._backend)

    def test_future_unsubscribe_rejected_without_losing_cleanup_owner(self):
        future = Future()
        remove = Mock(side_effect=[future, None])
        backend = Mock(spec=["call", "subscribe"], subscribe=Mock(return_value=remove))
        self.bind(backend, ("changed",))
        with self.assertRaisesRegex(RuntimeError, "cleanup needs retry"):
            self.session.stop()
        self.assertEqual(self.session._connections, [remove])
        future.set_result(None)
        self.session.stop()
        self.assertIsNone(self.session.manager)

    def test_async_result_completes_on_existing_timer(self):
        future = Future()
        self.bind(Mock(call=Mock(return_value=future)))
        self.session._message(self.message)
        self.renderer.call_result.assert_not_called()
        future.set_result({"isError": True, "content": []})
        self.session.tick()
        self.renderer.call_result.assert_called_once_with(
            "one", 1, "request", True, result={"isError": True, "content": []}
        )

    def test_surface_generation_change_cancels_request_without_stale_reply(self):
        future = Future()
        self.bind(Mock(call=Mock(return_value=future)))
        self.session._message(self.message)
        self.surface.generation = 2
        self.session.tick()
        self.assertTrue(future.cancelled())
        self.renderer.call_result.assert_not_called()

    def test_failed_unsubscribe_retained_for_retry(self):
        connection = Mock(dispose=Mock(side_effect=[RuntimeError("busy"), True]))
        backend = Mock(spec=["call", "on", "close"], on=Mock(return_value=connection))
        self.bind(backend, ("changed",))
        with self.assertRaisesRegex(RuntimeError, "cleanup needs retry"):
            self.session.stop()
        self.assertIs(self.session.manager, self.manager)
        self.assertEqual(self.session._connections, [connection.dispose])
        self.session.stop()
        self.assertIsNone(self.session.manager)
        backend.close.assert_not_called()

    def test_pending_owned_request_cancelled_at_close(self):
        future = Future()
        self.bind(Mock(call=Mock(return_value=future)))
        self.session._message(self.message)
        self.session.stop()
        self.assertTrue(future.cancelled())
        self.assertFalse(self.session._pending)

    def test_full_request_budget_rejects_before_starting_uncancellable_work(self):
        requests = [Future() for _ in range(33)]
        for request in requests:
            self.assertTrue(request.set_running_or_notify_cancel())
        backend = Mock(call=Mock(side_effect=requests))
        self.bind(backend)
        for index in range(32):
            self.session._message(dict(self.message, id=str(index)))
        self.assertEqual(backend.call.call_count, 32)
        self.renderer.call_result.assert_not_called()

        self.session._message(dict(self.message, id="over-budget"))
        self.assertEqual(backend.call.call_count, 32)
        self.assertEqual(len(self.session._pending), 32)
        self.assertTrue(requests[32].running())
        self.renderer.call_result.assert_called_once_with(
            "one",
            1,
            "over-budget",
            False,
            error={"name": "RuntimeError", "message": "Too many pending backend calls"},
        )

        with self.assertRaisesRegex(RuntimeError, "cleanup needs retry"):
            self.session.stop()
        self.assertIs(self.session.manager, self.manager)
        self.assertEqual([future for _, future in self.session._pending], requests[:32])
        self.assertTrue(all(request.running() for request in requests[:32]))
        backend.close.assert_not_called()
        backend.stop.assert_not_called()

        for request in requests[:32]:
            request.set_result(None)
        self.session.stop()
        self.assertFalse(self.session._pending)
        self.assertIsNone(self.session.manager)
        backend.close.assert_not_called()
        backend.stop.assert_not_called()

    def test_explicit_final_timeout_reaches_only_owned_surface_manager(self):
        backend = Mock()
        self.bind(backend)
        self.session.stop(timeout=3.0)
        self.manager.stop.assert_called_once_with(force=True, timeout=3.0)
        backend.close.assert_not_called()
        backend.stop.assert_not_called()

    def test_last_area_close_releases_connections_and_existing_timer_pump(self):
        connection = Mock(dispose=Mock(return_value=True))
        backend = Mock(spec=["call", "on", "close"], on=Mock(return_value=connection))
        self.bind(backend, ("changed",))
        self.manager.surfaces.clear()
        self.session.tick()
        connection.dispose.assert_called_once()
        self.scheduler.remove_pump.assert_called_once_with(self.session.tick)
        self.assertIsNone(self.session.manager)
        backend.close.assert_not_called()

    def test_backend_exception_fields_and_serialization_failure_are_returned(self):
        class ToolError(Exception):
            code = -32000
            data = {"retry": False}

        backend = Mock(call=Mock(side_effect=ToolError("failed")))
        self.bind(backend)
        self.session._message(self.message)
        error = self.renderer.call_result.call_args.kwargs["error"]
        self.assertEqual(error["code"], -32000)
        self.assertEqual(error["data"], {"retry": False})
        backend.call.side_effect = None
        backend.call.return_value = {"bad": object()}
        self.session._message(self.message)
        args = self.renderer.call_result.call_args
        self.assertFalse(args.args[3])
        self.assertEqual(args.kwargs["error"]["name"], "ResultSerializationError")


if __name__ == "__main__":
    unittest.main()
