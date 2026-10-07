"""Install a ZIP in an isolated Blender GUI and verify native addon lifecycle.

Launch with --factory-startup and isolated BLENDER_USER_CONFIG/SCRIPTS/EXTENSIONS.
Pass --extension, --output and --cleanup-marker after Blender's -- separator.
The marker requests final readback, cleanup and normal exit after UI observation.
This probe never imports Core or constructs a WebView.
"""

import argparse
import importlib
import json
import sys
import threading
import time
from pathlib import Path

import bpy

parser = argparse.ArgumentParser()
parser.add_argument("--extension", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--cleanup-marker", type=Path, required=True)
parser.add_argument("--expected-name")
parser.add_argument("--timeout", type=float, default=300)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
if bpy.app.background or args.output.exists() or args.cleanup_marker.exists():
    raise RuntimeError("Use a fresh isolated GUI and evidence paths")
report = {"blender": bpy.app.version_string, "webview_created": False, "checks": []}
started = time.monotonic()
addon = None
phase = 0
draws = []
initial_handlers = (len(bpy.app.handlers.load_pre), len(bpy.app.handlers.load_post))


def save():
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def check(name, passed):
    report["checks"].append({"name": name, "passed": bool(passed)})
    if not passed:
        raise AssertionError(name)


def draw_tool(layout, context):
    draws.append(threading.current_thread() is threading.main_thread())
    layout.label(text="Native consumer tool")
    if context.active_object is not None:
        layout.prop(context.active_object, "name", text="Probe object")


def advance():
    global addon, phase
    try:
        if phase == 0:
            repo_dir = args.output.parent / "extensions"
            repo_dir.mkdir()
            check(
                "create_isolated_local_repository",
                bpy.ops.preferences.extension_repo_add(
                    name="AuroraView Acceptance",
                    type="LOCAL",
                    use_custom_directory=True,
                    custom_directory=str(repo_dir),
                )
                == {"FINISHED"},
            )
            repo = next(
                r
                for r in bpy.context.preferences.extensions.repos
                if Path(r.directory).resolve() == repo_dir.resolve()
            )
            check(
                "install_and_enable_extension_zip",
                bpy.ops.extensions.package_install_files(
                    filepath=str(args.extension), repo=repo.module, enable_on_install=True
                )
                == {"FINISHED"},
            )
            addon = importlib.import_module(f"bl_ext.{repo.module}.auroraview_blender")
            check("extension_namespace", addon.__name__.startswith("bl_ext."))
            check("no_top_level_alias", "auroraview_blender" not in sys.modules)
            check("native_selection_panel", hasattr(bpy.types, "AURORAVIEW_PT_selection"))
            first = addon._session
            addon.register()
            check("idempotent_register", addon._session is first)
            addon.register_panel("ACCEPTANCE_PT_tool", "Consumer", draw_tool)
            check("consumer_panel_registered", hasattr(bpy.types, "ACCEPTANCE_PT_tool"))
            addon.unregister()
            check(
                "unregister_removes_all_panels",
                not hasattr(bpy.types, "ACCEPTANCE_PT_tool")
                and not hasattr(bpy.types, "AURORAVIEW_PT_selection"),
            )
            check("unregister_stops_timer", not first.scheduler.running)
            addon.register()
            previous = addon._session
            importlib.reload(addon)
            check(
                "reload_cleans_previous_session",
                not previous.scheduler.running and addon._session is None,
            )
            addon.register()
            addon.register_panel("ACCEPTANCE_PT_tool", "Consumer", draw_tool)
            report["old_dispatch"] = "captured"
            global old_dispatch
            old_dispatch = addon._session.scheduler.dispatcher()
            phase = 1
            file_path = args.output.parent / "lifecycle.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(file_path))
            bpy.ops.wm.open_mainfile(filepath=str(file_path))
            return 0.3
        if phase == 1:
            check("file_load_restarts_timer", addon._session.scheduler.running)
            try:
                old_dispatch(lambda: None)
            except RuntimeError as exc:
                check("file_load_expires_old_dispatcher", "expired" in str(exc))
            else:
                check("file_load_expires_old_dispatcher", False)
            check("core_not_imported", "auroraview" not in sys.modules)
            for area in bpy.context.screen.areas:
                if area.type == "VIEW_3D":
                    area.spaces.active.show_region_ui = True
            report["status"] = "awaiting_visual_check"
            save()
            phase = 2
            return 0.2
        if not args.cleanup_marker.exists():
            if time.monotonic() - started > args.timeout:
                raise TimeoutError("UI observation window expired")
            return 0.2
        report["active_object"] = bpy.context.active_object.name
        check("native_panel_draw_on_main_thread", bool(draws) and all(draws))
        if args.expected_name is not None:
            check("native_property_edit_readback", report["active_object"] == args.expected_name)
        addon.unregister()
        check(
            "final_panel_cleanup",
            not hasattr(bpy.types, "ACCEPTANCE_PT_tool")
            and not hasattr(bpy.types, "AURORAVIEW_PT_selection"),
        )
        check(
            "final_handler_cleanup",
            initial_handlers == (len(bpy.app.handlers.load_pre), len(bpy.app.handlers.load_post)),
        )
        check("final_session_cleanup", addon._session is None)
        report["status"] = "passed"
        save()
        bpy.ops.wm.quit_blender()
        return None
    except Exception as exc:
        report["status"], report["error"] = "failed", repr(exc)
        if addon is not None:
            try:
                addon.unregister()
            except Exception as cleanup_error:
                report["cleanup_error"] = repr(cleanup_error)
        save()
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(advance, first_interval=1.0, persistent=True)
