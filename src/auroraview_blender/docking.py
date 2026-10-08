"""Blender ownership of optional native Web surfaces and bounded scene calls."""

from __future__ import annotations

import inspect
import json
import os
import sys
import time
from pathlib import Path

from .demo import HTML
from .runtime import require_main_thread
from .scene_tools import BlenderSceneAdapter


def bundle_path(context=None):
    """Explicit user configuration; never download or install a renderer."""
    path = os.environ.get("AURORAVIEW_RENDERER_BUNDLE", "")
    if context is not None:
        addon = context.preferences.addons.get(__package__)
        if addon and addon.preferences.renderer_bundle:
            path = addon.preferences.renderer_bundle
    return Path(path).expanduser() if path else None


def docking_capabilities(context=None):
    import importlib.util

    python_supported = sys.platform == "win32" and sys.version_info >= (3, 12)
    client = importlib.util.find_spec("auroraview_offscreen") is not None
    bundle = bundle_path(context)
    configured = bundle is not None and (bundle / "bundle-manifest.json").is_file()
    reason = "Ready to open a native Web editor"
    if not python_supported:
        reason = "This native Web editor route requires Windows with Python 3.12+"
    elif not client:
        reason = "Install the extension package containing auroraview-offscreen"
    elif not configured:
        reason = "Select a verified renderer bundle in add-on preferences"
    return {
        "native_panel_embedding": True,
        "window_mode": "native_editor",
        "route_available": python_supported and client and configured,
        "route_verified": False,
        "reason": reason,
    }


class DockSession:
    """Own a lazy renderer and native surfaces; Blender remains the event-loop owner."""

    def __init__(self, bpy, scheduler, *, renderer_factory=None, manager_factory=None):
        self.bpy, self.scheduler = bpy, scheduler
        self.adapter = BlenderSceneAdapter(bpy)
        self.manager = None
        self._renderer_factory, self._manager_factory = renderer_factory, manager_factory
        self._last_scene = None
        self._next_scene_poll = 0.0
        self.last_error = None
        self._backend = None
        self._connections = []
        self._pending = []

    def _bind_backend(self, backend, events):
        if backend is self._backend:
            return
        if self.manager is not None or self._backend is not None:
            raise RuntimeError("Close native editors before changing their backend")
        if not callable(getattr(backend, "call", None)):
            raise TypeError("backend requires a public call(name, params) method")
        names = tuple(events)
        if len(names) > 32 or any(not isinstance(name, str) or not name for name in names):
            raise ValueError("Supply at most 32 named backend events")
        self._backend = backend
        try:
            for name in names:
                self._connections.append(
                    backend.on(name, lambda payload, event=name: self._emit(event, payload))
                )
        except BaseException:
            self.stop(force=True)
            raise

    def _emit(self, event, payload):
        require_main_thread()
        manager = self.manager
        if manager is not None and manager.renderer is not None:
            for surface in tuple(manager.surfaces.values()):
                manager.renderer.emit(surface.id, surface.generation, event, payload)

    def _factory(self):
        if self._renderer_factory:
            return self._renderer_factory
        from auroraview_offscreen import RendererProcess, resolve_bundle

        root = bundle_path(self.bpy.context)
        if root is None:
            raise RuntimeError("Select an AuroraView renderer bundle in add-on preferences")
        executable, helper = resolve_bundle(root)
        return lambda: RendererProcess(executable, helper)

    def open(self, context, *, split=True, html=None, url=None, backend=None, events=()):
        require_main_thread()
        if not self.scheduler.running:
            raise RuntimeError("Register AuroraView Blender before opening a native editor")
        if backend is not None:
            self._bind_backend(backend, events)
        try:
            if self.manager is None:
                from .surfaces import NativeSurfaceManager

                factory = self._manager_factory or NativeSurfaceManager
                self.manager = factory(self.bpy, self._factory(), on_message=self._message)
                self.scheduler.add_pump(self.tick)
            return self.manager.open(
                context, split=split, html=HTML if html is None and url is None else html, url=url
            )
        except BaseException:
            if self.manager is None or not self.manager.surfaces:
                self.stop(force=True)
            raise

    def _message(self, message):
        require_main_thread()
        if message["type"] == "error":
            self.last_error = message.get("message", "Renderer failed")
            return
        if message["type"] != "call":
            return
        manager = self.manager
        if manager is None:
            return
        try:
            if len(self._pending) >= 32:
                raise RuntimeError("Too many pending backend calls")
            call = self._backend.call if self._backend is not None else self.adapter.execute
            result = call(message["method"], message.get("params"))
            if callable(getattr(result, "done", None)) and callable(
                getattr(result, "cancel", None)
            ):
                self._pending.append((message, result))
                return
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError("The public backend adapter must schedule on its existing loop")
        except Exception as exc:
            self._reply(message, error=exc)
        else:
            self._reply(message, result=result)

    def _reply(self, message, *, result=None, error=None):
        manager = self.manager
        surface = manager.surfaces.get(message["surface_id"]) if manager else None
        if (
            surface is None
            or surface.generation != message["generation"]
            or manager.renderer is None
        ):
            return
        options = (
            {"result": result}
            if error is None
            else {"error": {"name": type(error).__name__, "message": str(error)}}
        )
        if error is not None:
            for key in ("code", "data"):
                value = getattr(error, key, None)
                if value is not None:
                    options["error"][key] = value
        try:
            encoded = json.dumps(options, allow_nan=False)
            if len(encoded.encode("utf-8")) > 900_000:
                raise ValueError("Backend result exceeds the native transport budget")
        except (TypeError, ValueError, OverflowError) as exc:
            error = exc
            options = {"error": {"name": "ResultSerializationError", "message": str(exc)}}
        manager.renderer.call_result(
            message["surface_id"], message["generation"], message["id"], error is None, **options
        )

    def tick(self):
        require_main_thread()
        manager = self.manager
        if manager is None:
            return
        manager.tick()
        for message, future in tuple(self._pending):
            surface = manager.surfaces.get(message["surface_id"])
            if surface is None or surface.generation != message["generation"]:
                future.cancel()
                if future.done():
                    self._pending.remove((message, future))
                continue
            if future.done():
                self._pending.remove((message, future))
                try:
                    result = future.result()
                except BaseException as exc:
                    self._reply(message, error=exc)
                else:
                    self._reply(message, result=result)
        if not manager.surfaces:
            self.stop(force=False)
            return
        if self._backend is not None:
            return
        if time.monotonic() < self._next_scene_poll or not manager.surfaces:
            return
        self._next_scene_poll = time.monotonic() + 0.25
        snapshot = self.adapter.execute("blender.scene.describe", {"limit": 128})
        signature = json.dumps(snapshot, sort_keys=True, allow_nan=False)
        if signature != self._last_scene:
            self._last_scene = signature
            for surface_id, surface in list(manager.surfaces.items()):
                manager.renderer.emit(
                    surface_id, surface.generation, "blender.scene.changed", snapshot
                )

    def stop(self, *, force=True, timeout=0.25):
        require_main_thread()
        errors = []
        for connection in tuple(self._connections):
            try:
                if connection.dispose():
                    self._connections.remove(connection)
            except Exception as exc:
                errors.append(exc)
        for record in tuple(self._pending):
            try:
                record[1].cancel()
                if record[1].done():
                    self._pending.remove(record)
            except Exception as exc:
                errors.append(exc)
        if self.manager is not None:
            self.manager.stop(force=force, timeout=timeout)
            if force and self.manager.needs_tick:
                errors.append(RuntimeError("Native renderer cleanup needs retry"))
            if errors or (force and (self._connections or self._pending)):
                raise RuntimeError("Native editor cleanup needs retry") from (
                    errors[0] if errors else None
                )
            if not self.manager.needs_tick and not self._connections and not self._pending:
                self.manager = None
                self.scheduler.remove_pump(self.tick)
        elif errors or self._connections or self._pending:
            raise RuntimeError("Backend connection cleanup needs retry") from (
                errors[0] if errors else None
            )
        if self.manager is None:
            self._backend = None  # Borrowed runtime/session remains owned by its caller.
        self._last_scene = None
