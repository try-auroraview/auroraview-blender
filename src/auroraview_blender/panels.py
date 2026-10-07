"""Blender-native docked tools, independent of AuroraView Core and WebView threads."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .runtime import require_main_thread

_PANEL_ID = re.compile(r"[A-Z][A-Z0-9_]*_PT_[A-Za-z][A-Za-z0-9_]*")


class NativePanelRegistry:
    """Own registered Sidebar panels and remove them without losing failed cleanup.

    ``draw(layout, context)`` runs on Blender's main thread. Draw callbacks should
    compose native layout controls; scene changes belong in native properties or
    operators, rather than being performed while drawing. No Core dependency or
    persistent Python worker is needed for these panels.
    """

    def __init__(self, bpy: Any):
        self._bpy = bpy
        self._panels: dict[str, type] = {}

    def register(
        self,
        panel_id: str,
        title: str,
        draw: Callable[[Any, Any], None],
        *,
        category: str = "AuroraView",
    ) -> type:
        """Register one native View3D Sidebar panel using a ``PREFIX_PT_name`` ID.

        Duplicate IDs fail before touching Blender. A failed Blender registration
        is not recorded; successful registrations stay owned until Blender accepts
        their removal. Relative module identity supports Blender's extension namespace.
        """
        require_main_thread()
        if (
            not isinstance(panel_id, str)
            or len(panel_id) > 64
            or _PANEL_ID.fullmatch(panel_id) is None
        ):
            raise ValueError(
                "panel_id must be a PREFIX_PT_name identifier of at most 64 characters"
            )
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a nonempty string")
        if not isinstance(category, str) or not category.strip():
            raise ValueError("category must be a nonempty string")
        if not callable(draw):
            raise TypeError("draw must be callable")
        if panel_id in self._panels:
            raise ValueError("Panel is already registered: " + panel_id)
        if getattr(self._bpy.types, panel_id, None) is not None:
            raise ValueError("Panel ID is already registered in Blender: " + panel_id)

        def draw_panel(panel: Any, context: Any) -> None:
            require_main_thread()
            draw(panel.layout, context)

        panel_type = type(
            panel_id,
            (self._bpy.types.Panel,),
            {
                "__module__": __name__,
                "bl_idname": panel_id,
                "bl_label": title,
                "bl_space_type": "VIEW_3D",
                "bl_region_type": "UI",
                "bl_category": category,
                "draw": draw_panel,
            },
        )
        self._bpy.utils.register_class(panel_type)
        self._panels[panel_id] = panel_type
        return panel_type

    def unregister(self, panel_id: str) -> bool:
        """Remove an owned panel; retain its class when Blender rejects removal."""
        require_main_thread()
        panel_type = self._panels.get(panel_id)
        if panel_type is None:
            return False
        self._bpy.utils.unregister_class(panel_type)
        del self._panels[panel_id]
        return True

    def clear(self) -> None:
        """Try every owned panel in reverse order and allow failed removal to retry."""
        require_main_thread()
        errors = []
        for panel_id in reversed(list(self._panels)):
            try:
                self.unregister(panel_id)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError("Could not unregister all AuroraView native panels") from errors[0]


def draw_selection_status(layout: Any, context: Any) -> None:
    """Draw live selection with Blender-owned editable controls, without importing Core."""
    require_main_thread()
    layout.label(text="Blender native tools", icon="BLENDER")
    layout.label(text="Mode: " + context.mode)
    layout.label(text="Selected objects: " + str(len(context.selected_objects)))
    active = context.active_object
    if active is None:
        layout.label(text="No active object", icon="INFO")
        return
    layout.prop(active, "name", text="Active object")
    layout.prop(active, "location", text="Location")
    rotation_property = {
        "QUATERNION": "rotation_quaternion",
        "AXIS_ANGLE": "rotation_axis_angle",
    }.get(getattr(active, "rotation_mode", "XYZ"), "rotation_euler")
    layout.prop(active, rotation_property, text="Rotation")
    layout.prop(active, "scale", text="Scale")
