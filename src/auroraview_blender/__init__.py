"""Blender add-on entry point. Importing does not load bpy or AuroraView Core."""

import atexit
import logging

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

__all__ = ["BlenderScheduler", "BlenderSession", "capabilities", "register", "unregister"]

_session = None
_classes = []
_handlers = []
_exit_callback = None


def register():
    """Register host UI and one timer. Repeated registration is harmless."""
    global _session, _classes, _handlers, _exit_callback
    from .runtime import require_main_thread

    require_main_thread()
    if _session is not None:
        return
    import bpy

    session = BlenderSession(bpy)

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
            report = capabilities()
            self.layout.label(text="Floating WebView tools")
            if not report["window_route_available"]:
                self.layout.label(text="WebView route not yet supported", icon="INFO")
            row = self.layout.row()
            row.enabled = report["window_route_available"]
            row.operator("auroraview.open")

    @bpy.app.handlers.persistent
    def on_load_pre(_):
        # File load invalidates scene context and non-persistent timers.
        session.stop()

    @bpy.app.handlers.persistent
    def on_load_post(_):
        session.start()

    def on_exit():
        try:
            session.stop()
        except Exception:
            # Blender may already have disposed its Python UI types. Core
            # receives close intent before the host timer is touched.
            logger.exception("AuroraView Blender cleanup during interpreter exit failed")

    classes = [AURORAVIEW_OT_open, AURORAVIEW_PT_tools]
    registered = []
    try:
        session.start()
        for cls in classes:
            bpy.utils.register_class(cls)
            registered.append(cls)
        bpy.app.handlers.load_pre.append(on_load_pre)
        bpy.app.handlers.load_post.append(on_load_post)
    except BaseException:
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)
        session.stop()
        raise
    _session, _classes = session, classes
    _handlers = [
        (bpy.app.handlers.load_pre, on_load_pre),
        (bpy.app.handlers.load_post, on_load_post),
    ]
    _exit_callback = on_exit
    atexit.register(on_exit)


def unregister():
    """Dispose host work before removing UI, without joining a native thread."""
    global _session, _classes, _handlers, _exit_callback
    from .runtime import require_main_thread

    require_main_thread()
    if _session is None:
        return
    import bpy

    session, classes, handlers = _session, _classes, _handlers
    _classes, _handlers = [], []
    try:
        session.stop()
        _session = None
        if _exit_callback is not None:
            atexit.unregister(_exit_callback)
            _exit_callback = None
    finally:
        for collection, handler in handlers:
            if handler in collection:
                collection.remove(handler)
        for cls in reversed(classes):
            bpy.utils.unregister_class(cls)
