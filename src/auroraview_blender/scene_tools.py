"""Bounded local scene example; production backends use their existing public port."""

from __future__ import annotations

import math
from collections import deque
from itertools import islice
from typing import Any

from .runtime import require_main_thread

_NAME = {"type": "string", "minLength": 1, "maxLength": 256}
_VECTOR = {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}}


def _spec(name, description, properties=None, required=()):
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties or {},
            "required": list(required),
            "additionalProperties": False,
        },
    }


TOOL_SPECS = (
    _spec("blender.capabilities", "Report the implemented host scene contract"),
    _spec(
        "blender.scene.describe",
        "Read at most 256 objects from the active scene",
        {
            "limit": {"type": "integer", "minimum": 1, "maximum": 256},
        },
    ),
    _spec(
        "blender.selection.set",
        "Select named scene objects on the Blender main thread",
        {
            "names": {"type": "array", "maxItems": 256, "items": _NAME},
            "active": _NAME,
        },
        ("names",),
    ),
    _spec(
        "blender.object.rename",
        "Rename a scene object to an unused name",
        {
            "name": _NAME,
            "new_name": _NAME,
        },
        ("name", "new_name"),
    ),
    _spec(
        "blender.object.transform",
        "Set finite local transforms (Euler rotation in radians)",
        {
            "name": _NAME,
            "location": _VECTOR,
            "rotation": _VECTOR,
            "scale": _VECTOR,
        },
        ("name",),
    ),
    _spec(
        "blender.timeline.set_frame",
        "Set an integer scene frame",
        {
            "frame": {"type": "integer", "minimum": -1048574, "maximum": 1048574},
        },
        ("frame",),
    ),
)
_SPECS = {item["name"]: item for item in TOOL_SPECS}


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or "\x00" in value:
        raise ValueError("Object names must contain 1–256 non-null characters")
    return value


def _vector(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("A transform must contain exactly three numbers")
    if any(
        type(item) not in (int, float) or not math.isfinite(item) or abs(item) > 1e6
        for item in value
    ):
        raise ValueError("Transform values must be finite numbers within +/- 1000000")
    return tuple(float(item) for item in value)


class BlenderSceneAdapter:
    """Local demo adapter; never starts a server, listener or event bus.

    Existing host bridges may call execute() through the addon scheduler's
    generation-bound dispatcher. Direct execution from a worker is rejected.
    The demo HTML and direct demo calls share these small scene examples.
    A production host supplies its existing BackendSession to open_editor().
    """

    dcc_name = "blender"
    capabilities = {
        "scene": True,
        "selection": True,
        "timeline": True,
        "undo": False,
        "render": False,
    }

    def __init__(self, bpy: Any):
        self.bpy = bpy
        self.revision = 0
        self._audit = deque(maxlen=64)

    def list_tools(self):
        # Return detached descriptors so a consumer cannot mutate the registry.
        import copy

        return copy.deepcopy(list(TOOL_SPECS))

    def get_context(self):
        return self.execute("blender.scene.describe", {"limit": 64})

    def get_audit_log(self):
        require_main_thread()
        return list(self._audit)

    def _object(self, name):
        obj = self.bpy.context.scene.objects.get(_name(name))
        if obj is None:
            raise ValueError(f"Object does not belong to the active scene: {name}")
        return obj

    @staticmethod
    def _describe_object(obj):
        return {
            "name": obj.name,
            "type": obj.type,
            "selected": bool(obj.select_get()),
            "location": list(obj.location),
            "rotation": list(obj.rotation_euler),
            "scale": list(obj.scale),
        }

    def execute(self, tool_name: str, parameters: dict | None = None):
        require_main_thread()
        spec = _SPECS.get(tool_name)
        if spec is None:
            raise ValueError(f"Unknown Blender capability: {tool_name}")
        parameters = {} if parameters is None else parameters
        if not isinstance(parameters, dict):
            raise TypeError("Tool parameters must be an object")
        schema = spec["parameters"]
        if set(parameters) - set(schema["properties"]):
            raise ValueError("Unknown tool parameter")
        if set(schema["required"]) - set(parameters):
            raise ValueError("Missing required tool parameter")
        try:
            result = self._execute(tool_name, parameters)
        except Exception:
            self._audit.append({"tool": tool_name, "ok": False, "revision": self.revision})
            raise
        self._audit.append({"tool": tool_name, "ok": True, "revision": self.revision})
        return result

    def _execute(self, name, params):
        scene = self.bpy.context.scene
        if name == "blender.capabilities":
            return {
                "host": "blender",
                "version": self.bpy.app.version_string,
                "main_thread": True,
                "scene_limit": 256,
                "capabilities": self.capabilities,
                "tools": self.list_tools(),
            }
        if name == "blender.scene.describe":
            limit = params.get("limit", 64)
            if type(limit) is not int or not 1 <= limit <= 256:
                raise ValueError("Scene limit must be an integer within 1–256")
            return {
                "scene": scene.name,
                "version": self.bpy.app.version_string,
                "revision": self.revision,
                "frame": scene.frame_current,
                "total_objects": len(scene.objects),
                "truncated": len(scene.objects) > limit,
                "objects": [self._describe_object(obj) for obj in islice(scene.objects, limit)],
            }
        if name == "blender.selection.set":
            names = params["names"]
            if not isinstance(names, list) or len(names) > 256:
                raise ValueError("Selection must contain at most 256 names")
            objects = [self._object(value) for value in names]
            active = self._object(params["active"]) if "active" in params else None
            if active is not None and active.name not in names:
                raise ValueError("Active object must belong to the requested selection")
            view_objects = self.bpy.context.view_layer.objects
            if any(view_objects.get(obj.name) is None for obj in objects):
                raise ValueError("Selected objects must belong to the active view layer")
            for obj in self.bpy.context.selected_objects:
                obj.select_set(False)
            for obj in objects:
                obj.select_set(True)
            view_objects.active = active or (objects[0] if objects else None)
            result = {
                "selected": [obj.name for obj in objects],
                "active": view_objects.active.name if view_objects.active else None,
            }
        elif name == "blender.object.rename":
            obj = self._object(params["name"])
            new_name = _name(params["new_name"])
            existing = self.bpy.data.objects.get(new_name)
            if existing is not None and existing != obj:
                raise ValueError("The requested object name already exists")
            obj.name = new_name
            result = self._describe_object(obj)
        elif name == "blender.object.transform":
            obj = self._object(params["name"])
            updates = {key: _vector(value) for key, value in params.items() if key != "name"}
            if not updates:
                raise ValueError("Supply at least one transform")
            for key, value in updates.items():
                setattr(obj, "rotation_euler" if key == "rotation" else key, value)
            result = self._describe_object(obj)
        elif name == "blender.timeline.set_frame":
            frame = params["frame"]
            if type(frame) is not int or not -1048574 <= frame <= 1048574:
                raise ValueError("Frame is outside Blender's supported integer range")
            scene.frame_set(frame)
            result = {"frame": scene.frame_current}
        else:
            raise ValueError("Unsupported tool")
        self.revision += 1
        return result
