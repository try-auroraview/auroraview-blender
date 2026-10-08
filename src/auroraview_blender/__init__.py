"""Blender add-on entry point. Importing does not load bpy or AuroraView Core."""

import atexit
import logging

from .panels import NativePanelRegistry, draw_selection_status
from .runtime import BlenderScheduler, BlenderSession, capabilities

logger = logging.getLogger(__name__)

# importlib.reload preserves the module dictionary. Dispose the previous
# registration before replacing its only references with a fresh generation.
if globals().get("_session") is not None:
    globals()["unregister"]()

bl_info = {
    "name": "AuroraView Blender",
    "author": "AuroraView contributors",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > AuroraView",
    "description": "Blender host adapter for AuroraView Core (development prototype)",
    "category": "3D View",
}

__all__ = [
    "BlenderScheduler",
    "BlenderSession",
    "capabilities",
    "register",
    "unregister",
    "register_panel",
    "unregister_panel",
    "open_editor",
    "get_agent_adapter",
]

_session = None
_classes = []
_handlers = []
_exit_callback = None
_panels = None
_registered = False
_docking = None


def open_editor(context=None, *, split=True, html=None, url=None, backend=None, events=()):
    """Mount HTML; an optional public BackendSession remains borrowed from its caller."""
    from .runtime import require_main_thread

    require_main_thread()
    if not _registered:
        raise RuntimeError("Register AuroraView Blender before opening a native editor")
    return _docking.open(
        context or _session.bpy.context,
        split=split,
        html=html,
        url=url,
        backend=backend,
        events=events,
    )


def get_agent_adapter():
    """Return the same bounded scene contract used by the HTML interface."""
    from .runtime import require_main_thread

    require_main_thread()
    if not _registered:
        raise RuntimeError("Register AuroraView Blender before requesting its agent contract")
    return _docking.adapter


def register_panel(panel_id, title, draw, *, category="AuroraView"):
    """Register a native sidebar tool; draw receives (layout, context) on main."""
    from .runtime import require_main_thread

    require_main_thread()
    if not _registered:
        raise RuntimeError("Register AuroraView Blender before registering a panel")
    return _panels.register(panel_id, title, draw, category=category)


def unregister_panel(panel_id):
    """Remove an owned native panel, retaining ownership if Blender rejects removal."""
    from .runtime import require_main_thread

    require_main_thread()
    return _panels.unregister(panel_id) if _panels is not None else False


def register():
    """Register host UI and one timer. Repeated registration is harmless."""
    global _session, _classes, _handlers, _exit_callback, _panels, _registered, _docking
    from .runtime import require_main_thread

    require_main_thread()
    if _registered:
        return
    if _session is not None:
        unregister()  # Retry any incomplete cleanup before creating new resources.
    import bpy

    session = BlenderSession(bpy)
    from .docking import DockSession, docking_capabilities
    from .surfaces import operator_classes

    docking = DockSession(bpy, session.scheduler)

    class AURORAVIEW_Preferences(bpy.types.AddonPreferences):
        bl_idname = __package__

        renderer_bundle: bpy.props.StringProperty(
            name="Renderer bundle",
            subtype="DIR_PATH",
            description="Extracted, verified AuroraView offscreen renderer bundle",
        )

        def draw(self, context):
            self.layout.prop(self, "renderer_bundle")
            self.layout.label(text="Windows native Web editors require Blender 5.1 or Python 3.12+")

    class AURORAVIEW_OT_dock(bpy.types.Operator):
        bl_idname = "auroraview.dock"
        bl_label = "Open Web Editor"
        bl_description = "Split this area and dock interactive AuroraView HTML in Blender"

        def execute(self, context):
            try:
                open_editor(context)
            except Exception as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            return {"FINISHED"}

    class AURORAVIEW_OT_close_editors(bpy.types.Operator):
        bl_idname = "auroraview.close_editors"
        bl_label = "Close Web Editors"
        bl_description = "Close owned Web editors and their renderer process"

        def execute(self, context):
            docking.stop(force=True)
            return {"FINISHED"}

    class AURORAVIEW_OT_open(bpy.types.Operator):
        bl_idname = "auroraview.open"
        bl_label = "Open AuroraView"
        bl_description = "Open a floating AuroraView tool window"

        def execute(self, context):
            try:
                session.open()
            except Exception as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            return {"FINISHED"}

    class AURORAVIEW_PT_tools(bpy.types.Panel):
        bl_label = "AuroraView"
        bl_idname = "AURORAVIEW_PT_tools"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "AuroraView"

        def draw(self, context):
            dock_report = docking_capabilities(context)
            self.layout.label(text="Native Web editor")
            self.layout.operator("auroraview.dock")
            if not dock_report["route_available"]:
                self.layout.label(text=dock_report["reason"], icon="INFO")
            if docking.manager is not None:
                self.layout.operator("auroraview.close_editors")
            self.layout.separator()
            report = capabilities()
            self.layout.label(text="Standalone Web window (experimental)")
            if not report["window_route_available"]:
                self.layout.label(text="WebView route not yet supported", icon="INFO")
            row = self.layout.row()
            row.enabled = report["window_route_available"]
            row.operator("auroraview.open")

    @bpy.app.handlers.persistent
    def on_load_pre(_):
        # File load invalidates scene context and non-persistent timers.
        docking.stop(force=True)
        session.stop()

    @bpy.app.handlers.persistent
    def on_load_post(_):
        session.start()

    def on_exit():
        try:
            docking.stop(force=True)
            session.stop()
        except Exception:
            # Blender may already have disposed its Python UI types. Core
            # receives close intent before the host timer is touched.
            logger.exception("AuroraView Blender cleanup during interpreter exit failed")

    _session = session
    _docking = docking
    _panels = NativePanelRegistry(bpy)
    _classes, _handlers = [], []
    try:
        session.start()
        for cls in [
            AURORAVIEW_Preferences,
            AURORAVIEW_OT_dock,
            AURORAVIEW_OT_close_editors,
            *operator_classes(lambda: docking.manager, bpy=bpy),
            AURORAVIEW_OT_open,
            AURORAVIEW_PT_tools,
        ]:
            bpy.utils.register_class(cls)
            _classes.append(cls)
        _panels.register("AURORAVIEW_PT_selection", "Selection", draw_selection_status)
        bpy.app.handlers.load_pre.append(on_load_pre)
        _handlers.append((bpy.app.handlers.load_pre, on_load_pre))
        bpy.app.handlers.load_post.append(on_load_post)
        _handlers.append((bpy.app.handlers.load_post, on_load_post))
    except BaseException:
        try:
            unregister()
        except Exception:
            logger.exception("AuroraView Blender registration rollback needs retry")
        raise
    _registered = True
    _exit_callback = on_exit
    atexit.register(on_exit)


def unregister():
    """Dispose host work before removing UI, without joining a native thread."""
    global _session, _classes, _handlers, _exit_callback, _panels, _registered, _docking
    from .runtime import require_main_thread

    require_main_thread()
    if _session is None:
        return
    import bpy

    _registered = False
    errors = []
    if _docking is not None:
        try:
            _docking.stop(force=True)
        except Exception as exc:
            errors.append(exc)
    try:
        _session.stop()
    except Exception as exc:
        errors.append(exc)
    for collection, handler in list(_handlers):
        try:
            if handler in collection:
                collection.remove(handler)
            _handlers.remove((collection, handler))
        except Exception as exc:
            errors.append(exc)
    if _panels is not None:
        try:
            _panels.clear()
        except Exception as exc:
            errors.append(exc)
    for cls in reversed(list(_classes)):
        try:
            bpy.utils.unregister_class(cls)
            _classes.remove(cls)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise RuntimeError(
            "AuroraView Blender cleanup is incomplete; retry unregister()"
        ) from errors[0]
    _session, _panels, _docking = None, None, None
    if _exit_callback is not None:
        atexit.unregister(_exit_callback)
        _exit_callback = None
