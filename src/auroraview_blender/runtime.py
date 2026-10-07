"""Blender-owned scheduling and session resources, with no private WebView engine."""

from __future__ import annotations

import logging
import queue
import re
import sys
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def require_main_thread() -> None:
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("This Blender operation must run on the main thread")


class BlenderScheduler:
    """One Blender timer drains a bounded thread-safe queue.

    start/stop touch bpy only on the main thread. submit never calls bpy, so RPC
    delivery from a WebView thread cannot register a Blender timer off-thread.
    """

    def __init__(self, bpy: Any, interval: float = 0.01, batch_size: int = 32):
        if interval <= 0 or batch_size < 1:
            raise ValueError("interval and batch_size must be positive")
        self._bpy = bpy
        self._interval = interval
        self._batch_size = batch_size
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue(maxsize=1024)
        self._lock = threading.Lock()
        self._running = False
        self._generation = 0
        self._callback = self._tick
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def start(self) -> None:
        require_main_thread()
        if self._bpy.app.background:
            raise RuntimeError("AuroraView Blender requires an interactive GUI session")
        with self._lock:
            if self._running:
                return
            self._bpy.app.timers.register(
                self._callback, first_interval=self._interval, persistent=False
            )
            self._generation += 1
            self._running = True

    def submit(self, callback: Callable[[], None]) -> None:
        self._submit(callback)

    def dispatcher(self) -> Callable[[Callable[[], None]], None]:
        """Bind a Core dispatcher to this registration, never a later file/session."""
        require_main_thread()
        with self._lock:
            if not self._running:
                raise RuntimeError("Blender session is closed")
            generation = self._generation

        def dispatch(callback: Callable[[], None]) -> None:
            self._submit(callback, generation)

        return dispatch

    def _submit(self, callback: Callable[[], None], generation: int | None = None) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            if not self._running:
                raise RuntimeError("Blender session is closed")
            if generation is not None and generation != self._generation:
                raise RuntimeError("Blender dispatcher belongs to an expired session")
            try:
                self._queue.put_nowait(callback)
            except queue.Full as exc:
                raise RuntimeError("Blender callback queue is full") from exc

    def _tick(self) -> float | None:
        require_main_thread()
        if not self._running:
            return None
        for _ in range(self._batch_size):
            try:
                callback = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("Blender callback failed")
            if not self._running:
                return None
        return self._interval

    def stop(self) -> None:
        require_main_thread()
        detached = []
        try:
            with self._lock:
                self._running = False
                try:
                    if self._bpy.app.timers.is_registered(self._callback):
                        self._bpy.app.timers.unregister(self._callback)
                finally:
                    while True:
                        try:
                            detached.append(self._queue.get_nowait())
                        except queue.Empty:
                            break
        finally:
            detached.clear()  # Callable finalizers may reenter the scheduler.


def capabilities(platform: str | None = None) -> dict[str, Any]:
    """Report implemented routes without claiming platform GUI certification."""
    platform = sys.platform if platform is None else platform
    supported = platform == "win32"
    return {
        "host": "blender",
        "platform": platform,
        "main_thread_dispatch": True,
        "native_tools_panel": True,
        "native_tools_require_core": False,
        "native_panel_embedding": False,
        "window_mode": "floating",
        "window_route_available": supported,
        "window_route_verified": False,
        "reason": (
            "Windows owner-thread route requires the patched AuroraView Core"
            if supported
            else "A nonblocking native WebView route is not validated on this platform"
        ),
    }


_DEFAULT_HTML = """<!doctype html><html><meta charset="utf-8">
<title>AuroraView Blender</title><body><h1>AuroraView Blender</h1>
<button id="inspect">Read Blender context</button><pre id="result">Bridge waiting</pre>
<script>
document.getElementById('inspect').onclick = async () => {
  try {
    const value = await window.auroraview.call('blender.context');
    document.getElementById('result').textContent = JSON.stringify(value, null, 2);
  } catch (error) { document.getElementById('result').textContent = String(error); }
};
</script></body></html>"""


class BlenderSession:
    """Own Blender callbacks and view references; Core owns transport/lifecycle."""

    def __init__(self, bpy: Any, view_factory: Callable[..., Any] | None = None):
        self.bpy = bpy
        self.scheduler = BlenderScheduler(bpy)
        self.views: dict[str, Any] = {}
        self._view_factory = view_factory

    def start(self) -> None:
        require_main_thread()
        if not self.scheduler.running and self.views:
            # A failed close may retain a live view bound to the old generation.
            # Complete that cleanup before any new host work can be accepted.
            self.stop()
        self.scheduler.start()

    def context(self) -> dict[str, Any]:
        require_main_thread()
        return {
            "host": "blender",
            "version": self.bpy.app.version_string,
            "main_thread": True,
            "selected_objects": [obj.name for obj in self.bpy.context.selected_objects],
        }

    def open(
        self,
        view_id: str = "main",
        *,
        configure: Callable[[Any], None] | None = None,
        **options: Any,
    ) -> Any:
        """Open a view, configuring its public Core API before native show.

        ``configure(view)`` runs synchronously on Blender's main thread after
        the call dispatcher and default binding are installed, before
        ``show(wait=False)``. Use it to bind commands and events; do not show
        or close the view in this callback.

        A live ``view_id`` is returned unchanged: neither ``configure`` nor
        new options are applied. After close (or a dead view), a fresh view is
        created; supply ``configure`` again to register its bindings. The
        callback is not stored by the session. On failure the new view is
        closed; if close itself fails, it is retained for ``close``/``stop``
        to retry. The already-started session and other views remain owned.
        """
        require_main_thread()
        if configure is not None and not callable(configure):
            raise TypeError("configure must be callable or None")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", view_id):
            raise ValueError(
                "view_id must contain 1–64 letters, digits, dots, dashes or underscores"
            )
        report = capabilities()
        if not report["window_route_available"]:
            raise RuntimeError(report["reason"])
        if not self.scheduler.running:
            raise RuntimeError("Register the Blender session before opening a view")
        existing = self.views.get(view_id)
        if existing is not None and existing.is_alive():
            return existing
        if existing is not None:
            self.close(view_id)
        factory = self._view_factory
        if factory is None:
            from auroraview import WebView

            if not all(
                hasattr(WebView, method) for method in ("set_call_dispatcher", "request_close")
            ):
                raise RuntimeError(
                    "Install an AuroraView Core build with the host lifecycle contract"
                )
            factory = WebView
        options.setdefault("title", "AuroraView Blender · " + view_id)
        if "html" not in options and "url" not in options:
            options["html"] = _DEFAULT_HTML
        options["dcc_mode"] = True
        view = factory(**options)
        try:
            # Require the shared contract explicitly rather than monkeypatching
            # private Core fields or creating a second bridge in this package.
            view.set_call_dispatcher(self.scheduler.dispatcher())
            view.bind_call("blender.context", self.context)
            if configure is not None:
                configure(view)
            view.show(wait=False)
        except BaseException:
            try:
                view.request_close()
            except BaseException:
                # Keep ownership when Core reports incomplete close delivery.
                self.views[view_id] = view
                raise
            raise
        self.views[view_id] = view
        return view

    def close(self, view_id: str) -> None:
        require_main_thread()
        view = self.views.get(view_id)
        if view is not None:
            view.request_close()
            self.views.pop(view_id, None)

    def stop(self) -> None:
        require_main_thread()
        errors = []
        try:
            for view_id in list(self.views):
                try:
                    self.close(view_id)
                except Exception as exc:
                    errors.append(exc)
        finally:
            self.scheduler.stop()
        if errors:
            raise RuntimeError("Could not close all AuroraView windows") from errors[0]
