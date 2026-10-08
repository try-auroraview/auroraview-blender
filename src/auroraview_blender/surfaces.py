"""Blender-owned IMAGE_EDITOR surfaces for an optional offscreen renderer."""

from __future__ import annotations

import logging
import math
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from .input import InputState, translate

logger = logging.getLogger(__name__)
MAX_PIXELS = 4 * 1024 * 1024
MAX_DIMENSION = 4096
MAX_SURFACES = 8
EDGE_MARGIN = 3
# Renderer rows start at the top; GPU texture coordinates start at the bottom.
QUAD_UVS = ((0, 1), (1, 1), (1, 0), (0, 0))


def _main() -> None:
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Blender surfaces require the main thread")


def surface_capabilities(
    platform: str | None = None, python_version: tuple[int, int] | None = None
) -> dict[str, Any]:
    platform = sys.platform if platform is None else platform
    python_version = sys.version_info[:2] if python_version is None else python_version
    supported = platform == "win32" and python_version >= (3, 12)
    return {
        "native_offscreen_surface": supported,
        "verified": False,
        "reason": (
            "Windows Python 3.12+ nonblocking pipes and an explicitly configured "
            "renderer are required"
        ),
    }


def _size(region: Any, ui_scale: float = 1.0) -> tuple[int, int]:
    width, height = max(1, region.width / ui_scale), max(1, region.height / ui_scale)
    scale = min(
        1, MAX_DIMENSION / width, MAX_DIMENSION / height, math.sqrt(MAX_PIXELS / (width * height))
    )
    return max(1, int(width * scale)), max(1, int(height * scale))


@dataclass
class _Surface:
    id: str
    generation: int
    pointers: tuple[int, int, int, int]
    previous_type: str
    width: int
    height: int
    ui_scale: float = 1.0
    resize_revision: int = 0
    sequence: int = -1
    frame_count: int = 0
    frame: dict[str, Any] | None = None
    texture: Any = None
    uploaded_sequence: int = -1
    input_state: InputState = field(default_factory=InputState)
    input_events: int = 0
    last_input: dict[str, Any] | None = None
    error: str | None = None


class NativeSurfaceManager:
    """Own area identities, input, textures and process lifecycle on Blender's main thread."""

    def __init__(
        self,
        bpy: Any,
        renderer_factory: Callable[[], Any],
        *,
        on_message: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.bpy = bpy
        self._factory = renderer_factory
        self._on_message = on_message
        self._client: Any = None
        self._closing: list[Any] = []
        self._surfaces: dict[str, _Surface] = {}
        self._generation = 0
        self._draw_handler: Any = None
        self._modal_windows: set[int] = set()
        self._stopping = False
        self.last_error: str | None = None
        self.last_closed_info: dict[str, Any] | None = None

    @property
    def surfaces(self) -> Mapping[str, _Surface]:
        return MappingProxyType(self._surfaces)

    @property
    def renderer(self) -> Any:
        return self._client

    @property
    def needs_tick(self) -> bool:
        return bool(self._surfaces or self._client or self._closing or self._draw_handler)

    def get_info(self, surface_id: str) -> dict[str, Any]:
        _main()
        surface = self._surfaces[surface_id]
        binding = self._lookup(surface)
        region = binding[2] if binding is not None else None
        return {
            "surface_id": surface.id,
            "generation": surface.generation,
            "window_pointer": surface.pointers[0],
            "area_pointer": surface.pointers[1],
            "region_pointer": surface.pointers[2],
            "region": None
            if region is None
            else {"x": region.x, "y": region.y, "width": region.width, "height": region.height},
            "width": surface.width,
            "height": surface.height,
            "ui_scale": surface.ui_scale,
            "sequence": surface.sequence,
            "frame_count": surface.frame_count,
            "uploaded_sequence": surface.uploaded_sequence,
            "input_events": surface.input_events,
            "last_input": None if surface.last_input is None else dict(surface.last_input),
            "error": surface.error,
        }

    def _error(self, exc: Exception | str) -> None:
        self.last_error = str(exc)
        logger.warning("Native surface: %s", exc)

    def _notify(self, message: dict[str, Any]) -> None:
        if self._on_message is not None:
            try:
                self._on_message(message)
            except Exception as exc:
                self._error(exc)

    def _ui_scale(self) -> float:
        try:
            scale = float(self.bpy.context.preferences.system.ui_scale)
        except (AttributeError, TypeError, ValueError, ReferenceError):
            return 1.0
        return scale if math.isfinite(scale) and scale > 0 else 1.0

    def _lookup(self, surface: _Surface) -> tuple[Any, Any, Any] | None:
        window_id, area_id, region_id, space_id = surface.pointers
        for window in self.bpy.context.window_manager.windows:
            if window.as_pointer() != window_id:
                continue
            for area in window.screen.areas:
                if area.as_pointer() != area_id or area.type != "IMAGE_EDITOR":
                    continue
                if area.spaces.active.as_pointer() != space_id:
                    continue
                for region in area.regions:
                    if region.type == "WINDOW" and region.as_pointer() == region_id:
                        return window, area, region
        return None

    def open(
        self, context: Any, *, split: bool = True, html: str | None = None, url: str | None = None
    ) -> str:
        _main()
        if not surface_capabilities()["native_offscreen_surface"]:
            raise RuntimeError(surface_capabilities()["reason"])
        if self.bpy.app.background:
            raise RuntimeError("Native surfaces require an interactive Blender window")
        if self._stopping:
            raise RuntimeError("Surface manager is stopping")
        if len(self._surfaces) >= MAX_SURFACES:
            raise RuntimeError("Too many native surfaces")
        if (html is None) == (url is None):
            raise ValueError("Supply exactly one of html or url")
        window, area = context.window, context.area
        if window is None or area is None:
            raise RuntimeError("Open a surface from a native editor area")
        if split:
            before = {item.as_pointer() for item in window.screen.areas}
            with self.bpy.context.temp_override(window=window, area=area):
                self.bpy.ops.screen.area_split(direction="VERTICAL", factor=0.5)
            added = [item for item in window.screen.areas if item.as_pointer() not in before]
            if len(added) != 1:
                raise RuntimeError("Blender did not create exactly one split area")
            area = added[0]
        if any(
            surface.pointers[:2] == (window.as_pointer(), area.as_pointer())
            for surface in self._surfaces.values()
        ):
            raise RuntimeError("This area already owns an AuroraView surface")
        previous_type = area.type
        area.type = "IMAGE_EDITOR"
        region = next((item for item in area.regions if item.type == "WINDOW"), None)
        if region is None:
            area.type = previous_type
            raise RuntimeError("Image editor has no WINDOW region")
        self._generation += 1
        surface_id = uuid4().hex
        ui_scale = self._ui_scale()
        width, height = _size(region, ui_scale)
        surface = _Surface(
            surface_id,
            self._generation,
            (
                window.as_pointer(),
                area.as_pointer(),
                region.as_pointer(),
                area.spaces.active.as_pointer(),
            ),
            previous_type,
            width,
            height,
            ui_scale=ui_scale,
        )
        self._surfaces[surface_id] = surface
        try:
            if self._client is None:
                self._client = self._factory()
            self._client.create(
                surface_id, surface.generation, width=width, height=height, html=html, url=url
            )
            if self._draw_handler is None:
                self._draw_handler = self.bpy.types.SpaceImageEditor.draw_handler_add(
                    self._draw, (), "WINDOW", "POST_PIXEL"
                )
            if window.as_pointer() not in self._modal_windows:
                with self.bpy.context.temp_override(window=window, area=area, region=region):
                    result = self.bpy.ops.auroraview.surface_input("INVOKE_DEFAULT")
                if "RUNNING_MODAL" not in result:
                    raise RuntimeError("Surface input operator did not start")
                self._modal_windows.add(window.as_pointer())
            area.tag_redraw()
        except Exception:
            self.close(surface_id)
            raise
        return surface_id

    def _accept_frame(self, surface: _Surface, message: dict[str, Any]) -> None:
        width, height, stride = message.get("width"), message.get("height"), message.get("stride")
        payload, sequence = message.get("payload"), message.get("seq")
        if (width, height) != (surface.width, surface.height) or message.get(
            "resize_revision"
        ) != surface.resize_revision:
            return
        if (
            message.get("format") != "rgba8"
            or message.get("alpha") != "straight"
            or message.get("origin") != "top-left"
        ):
            return
        if type(sequence) is not int or sequence < 1 or sequence <= surface.sequence:
            return
        if (
            not isinstance(stride, int)
            or stride < width * 4
            or not isinstance(payload, bytes)
            or len(payload) != stride * height
            or len(payload) > MAX_PIXELS * 4
        ):
            return
        surface.frame, surface.sequence = message, sequence
        surface.frame_count += 1
        surface.error = None

    def tick(self) -> bool:
        _main()
        if self._client is not None:
            try:
                messages = self._client.poll()
            except Exception as exc:
                self._error(exc)
                self._retire_client()
                messages = []
            for message in messages:
                surface = self._surfaces.get(message.get("surface_id"))
                if message.get("type") == "error" and not message.get("surface_id"):
                    self.last_error = str(
                        message.get("error", message.get("message", "Renderer error"))
                    )
                    self._notify(message)
                if surface is None or message.get("generation") != surface.generation:
                    continue
                if message.get("type") == "frame":
                    self._accept_frame(surface, message)
                else:
                    if message.get("type") == "error":
                        surface.error = str(
                            message.get("message", message.get("error", "Renderer error"))
                        )
                    self._notify(message)
            if self._client is None or not self._client.alive:
                self.last_error = self.last_error or "Offscreen renderer exited"
                self._retire_client()
                for surface_id, surface in tuple(self._surfaces.items()):
                    surface.error = self.last_error
                    self.close(surface_id)
                self._modal_windows.clear()
        for surface_id, surface in tuple(self._surfaces.items()):
            binding = self._lookup(surface)
            if binding is None:
                self.close(surface_id)
                continue
            _, area, region = binding
            surface.ui_scale = self._ui_scale()
            size = _size(region, surface.ui_scale)
            if size != (surface.width, surface.height) and self._client is not None:
                surface.width, surface.height = size
                surface.resize_revision += 1
                surface.frame = None
                try:
                    self._client.resize(
                        surface.id, surface.generation, width=surface.width, height=surface.height
                    )
                except Exception as exc:
                    surface.error = str(exc)
                    self._error(exc)
            area.tag_redraw()
        if not self._surfaces:
            self._detach_handler()
            self._retire_client()
        self._closing = [client for client in self._closing if self._pump_closing(client)]
        return self.needs_tick

    def _pump_closing(self, client: Any) -> bool:
        try:
            client.poll()
        except Exception as exc:
            self._error(exc)
            try:
                client.terminate()
            except Exception as terminate_error:
                self._error(terminate_error)
                return True
        return not client.closed

    def _detach_handler(self) -> None:
        if self._draw_handler is not None:
            try:
                self.bpy.types.SpaceImageEditor.draw_handler_remove(self._draw_handler, "WINDOW")
            except ValueError:
                # Blender may already invalidate draw handlers during file load.
                self._draw_handler = None
            except Exception as exc:
                # Retain ownership so the next tick or stop can retry removal.
                self._error(exc)
            else:
                self._draw_handler = None

    def _retire_client(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            self._closing.append(client)
            try:
                client.shutdown()
            except Exception as exc:
                self._error(exc)
                try:
                    client.terminate()
                except Exception as terminate_error:
                    self._error(terminate_error)

    def _draw(self) -> None:
        _main()
        context = self.bpy.context
        if context.window is None or context.area is None or context.region is None:
            return
        pointers = (
            context.window.as_pointer(),
            context.area.as_pointer(),
            context.region.as_pointer(),
            context.area.spaces.active.as_pointer(),
        )
        for surface in self._surfaces.values():
            if (
                pointers == surface.pointers
                and context.area.type == "IMAGE_EDITOR"
                and context.region.type == "WINDOW"
            ):
                try:
                    self._draw_surface(surface, context.region)
                except Exception as exc:
                    surface.error = str(exc)
                    logger.exception("Native surface draw failed")
                break

    @staticmethod
    def _draw_surface(surface: _Surface, region: Any) -> None:
        _main()
        if surface.frame is None:
            return
        import gpu
        import numpy as np
        from gpu_extras.batch import batch_for_shader

        if surface.uploaded_sequence != surface.sequence:
            frame = surface.frame
            pixels = np.ndarray(
                (surface.height, surface.width, 4),
                dtype=np.uint8,
                buffer=frame["payload"],
                strides=(frame["stride"], 4, 1),
            )
            normalized = np.multiply(pixels, np.float32(1 / 255), dtype=np.float32)
            data = gpu.types.Buffer("FLOAT", normalized.shape, normalized)
            surface.texture = gpu.types.GPUTexture(
                (surface.width, surface.height), format="RGBA8", data=data
            )
            surface.uploaded_sequence = surface.sequence
        shader = gpu.shader.from_builtin("IMAGE")
        batch = batch_for_shader(
            shader,
            "TRI_FAN",
            {
                "pos": (
                    (0, 0),
                    (region.width, 0),
                    (region.width, region.height),
                    (0, region.height),
                ),
                "texCoord": QUAD_UVS,
            },
        )
        blend = gpu.state.blend_get()
        try:
            gpu.state.blend_set("ALPHA")
            shader.bind()
            shader.uniform_sampler("image", surface.texture)
            batch.draw(shader)
        finally:
            gpu.state.blend_set(blend)

    def handle_event(self, window_id: int, event: Any) -> bool:
        _main()
        consumed = False
        for surface in self._surfaces.values():
            if surface.pointers[0] != window_id:
                continue
            binding = self._lookup(surface)
            if binding is None:
                continue
            _, area, region = binding
            mx, my = getattr(event, "mouse_x", -1), getattr(event, "mouse_y", -1)
            inside = (
                region.x + EDGE_MARGIN <= mx < region.x + region.width - EDGE_MARGIN
                and region.y + EDGE_MARGIN <= my < region.y + region.height - EDGE_MARGIN
            )
            if inside:
                inside = not any(
                    item.type != "WINDOW"
                    and item.width > 1
                    and item.height > 1
                    and item.x <= mx < item.x + item.width
                    and item.y <= my < item.y + item.height
                    for item in area.regions
                )
            x = (mx - region.x) * surface.width / max(1, region.width)
            y = (region.height - 1 - (my - region.y)) * surface.height / max(1, region.height)
            events, handled = translate(event, surface.input_state, inside=inside, x=x, y=y)
            surface.input_events += 1
            # Keep one bounded event summary, never committed text or Unicode input.
            surface.last_input = {
                "type": str(getattr(event, "type", ""))[:32],
                "value": str(getattr(event, "value", ""))[:16],
                "inside": inside,
                "x": x,
                "y": y,
                "handled": handled,
            }
            if self._client is not None:
                for item in events:
                    try:
                        self._client.input(surface.id, surface.generation, item)
                    except Exception as exc:
                        surface.error = str(exc)
                        self._error(exc)
            consumed |= handled
        return consumed

    def close(self, surface_id: str) -> None:
        _main()
        surface = self._surfaces.pop(surface_id, None)
        if surface is None:
            return
        try:
            binding = self._lookup(surface)
        except Exception as exc:
            self._error(exc)
            binding = None
        self.last_closed_info = {
            "surface_id": surface.id,
            "generation": surface.generation,
            "sequence": surface.sequence,
            "frame_count": surface.frame_count,
            "ui_scale": surface.ui_scale,
            "uploaded_sequence": surface.uploaded_sequence,
            "input_events": surface.input_events,
            "last_input": None if surface.last_input is None else dict(surface.last_input),
            "error": surface.error,
        }
        try:
            if self._client is not None:
                for event in surface.input_state.release():
                    try:
                        self._client.input(surface.id, surface.generation, event)
                    except Exception as exc:
                        self._error(exc)
                try:
                    self._client.close_surface(surface.id, surface.generation)
                except Exception as exc:
                    self._error(exc)
        finally:
            surface.texture = surface.frame = None
            if binding is not None:
                try:
                    binding[1].type = surface.previous_type
                    binding[1].tag_redraw()
                except Exception as exc:
                    self._error(exc)
            if not self._surfaces:
                self._detach_handler()
                self._retire_client()

    def stop(self, *, force: bool = False) -> None:
        _main()
        self._stopping = True
        try:
            for surface_id in tuple(self._surfaces):
                try:
                    self.close(surface_id)
                except Exception as exc:
                    self._error(exc)
        finally:
            self._modal_windows.clear()
            self._detach_handler()
            self._retire_client()
            if force:
                remaining = []
                for client in self._closing:
                    try:
                        client.terminate()
                    except Exception as exc:
                        self._error(exc)
                        remaining.append(client)
                    else:
                        if not client.closed:
                            remaining.append(client)
                self._closing = remaining


def operator_classes(
    manager_getter: Callable[[], NativeSurfaceManager | None], *, bpy: Any = None
) -> tuple[type, ...]:
    """Root add-on owns registration; modal handlers stay bound to one manager/window."""
    if bpy is None:
        import bpy

    class AURORAVIEW_OT_surface_input(bpy.types.Operator):
        bl_idname = "auroraview.surface_input"
        bl_label = "AuroraView Surface Input"
        bl_options = {"INTERNAL"}

        def invoke(self, context: Any, event: Any) -> set[str]:
            manager = manager_getter()
            if manager is None or context.window is None:
                return {"CANCELLED"}
            self._manager = manager
            self._window_id = context.window.as_pointer()
            context.window_manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}

        def modal(self, context: Any, event: Any) -> set[str]:
            manager = manager_getter()
            if manager is not self._manager or not any(
                item.pointers[0] == self._window_id for item in manager._surfaces.values()
            ):
                self._manager._modal_windows.discard(self._window_id)
                return {"CANCELLED"}
            if context.window is None or context.window.as_pointer() != self._window_id:
                return {"PASS_THROUGH"}
            return (
                {"RUNNING_MODAL"}
                if manager.handle_event(self._window_id, event)
                else {"PASS_THROUGH"}
            )

        def cancel(self, context: Any) -> None:
            self._manager._modal_windows.discard(self._window_id)

    return (AURORAVIEW_OT_surface_input,)
