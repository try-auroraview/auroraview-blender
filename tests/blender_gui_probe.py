"""Real Blender host-adapter test; deliberately does not create a WebView."""

import argparse
import json
import sys
import threading
import time
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import auroraview_blender as addon

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--cleanup-marker", type=Path, required=True)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
if sys.platform != "linux":
    raise RuntimeError("This probe validates the Linux host adapter and fail-closed window route")
if args.output.exists() or args.cleanup_marker.exists():
    raise RuntimeError("Use new evidence paths for each probe run")
if not args.output.parent.is_dir():
    raise RuntimeError("Create the output directory before launching Blender")
OUT = args.output
CONTROL = args.cleanup_marker
report = {
    "probe": "actual_blender_addon_and_host_queue",
    "native_webview_test": False,
    "native_webview_started": False,
    "blender": bpy.app.version_string,
    "python": sys.version,
    "background": bpy.app.background,
    "checks": [],
    "status": "running",
}
started = time.monotonic()
baseline_pre = len(bpy.app.handlers.load_pre)
baseline_post = len(bpy.app.handlers.load_post)
results = []
worker_errors = []
late_calls = []
phase = 0


def save():
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def check(name, passed, **details):
    report["checks"].append({"name": name, "passed": bool(passed), **details})


check("real_gui", not bpy.app.background and len(bpy.context.window_manager.windows) == 1)
addon.register()
session = addon._session
timer = session.scheduler._callback
addon.register()
check("repeat_registration_single_session", addon._session is session)
check("scheduler_is_native_timer", bpy.app.timers.is_registered(timer))
check("one_load_pre_handler", len(bpy.app.handlers.load_pre) == baseline_pre + 1)
check("one_load_post_handler", len(bpy.app.handlers.load_post) == baseline_post + 1)
check(
    "operator_registered", bpy.ops.auroraview.open.get_rna_type().identifier == "AURORAVIEW_OT_open"
)
check("panel_registered", hasattr(bpy.types, "AURORAVIEW_PT_tools"))


def worker():
    try:
        session.scheduler.submit(
            lambda: results.append(
                {
                    "context": session.context(),
                    "actual_main_thread": threading.current_thread() is threading.main_thread(),
                }
            )
        )
    except Exception as exc:
        worker_errors.append(str(exc))


thread = threading.Thread(target=worker, name="AuroraView Blender probe producer")
thread.start()
thread.join(timeout=1)
check("producer_exited_without_host_wait", not thread.is_alive())
save()


def advance():
    global phase
    try:
        if phase == 0:
            if not results and not worker_errors:
                if time.monotonic() - started > 15:
                    raise TimeoutError("Blender host queue did not execute")
                return 0.1
            check("worker_submit_succeeded", not worker_errors, errors=worker_errors)
            check(
                "queued_context_runs_on_actual_main_thread",
                bool(results) and results[0]["actual_main_thread"],
                results=results,
            )
            try:
                session.open("linux-probe")
            except RuntimeError as exc:
                check(
                    "linux_window_route_fails_closed", "not validated" in str(exc), reason=str(exc)
                )
            else:
                check("linux_window_route_fails_closed", False)
            check("core_was_not_imported", "auroraview" not in sys.modules)
            session.scheduler.submit(lambda: late_calls.append(True))
            addon.unregister()
            addon.unregister()
            check("disable_removes_host_timer", not bpy.app.timers.is_registered(timer))
            check("disable_discards_queue", session.scheduler.pending == 0)
            check("disable_removes_panel", not hasattr(bpy.types, "AURORAVIEW_PT_tools"))
            check(
                "disable_removes_file_handlers",
                len(bpy.app.handlers.load_pre) == baseline_pre
                and len(bpy.app.handlers.load_post) == baseline_post,
            )
            phase = 1
            return 0.15
        if phase == 1:
            check("cancelled_work_never_executed", not late_calls)
            addon.register()
            check("reenable_uses_fresh_session", addon._session is not session)
            report["status"] = "awaiting_visual_check"
            save()
            phase = 2
            return 0.2
        if not CONTROL.exists():
            if time.monotonic() - started > 180:
                raise TimeoutError("Visual observation window expired")
            return 0.2
        final_timer = addon._session.scheduler._callback
        addon.unregister()
        check("final_cleanup_timer_removed", not bpy.app.timers.is_registered(final_timer))
        check("final_cleanup_panel_removed", not hasattr(bpy.types, "AURORAVIEW_PT_tools"))
        check(
            "final_cleanup_handlers_removed",
            len(bpy.app.handlers.load_pre) == baseline_pre
            and len(bpy.app.handlers.load_post) == baseline_post,
        )
        report["status"] = "passed" if all(c["passed"] for c in report["checks"]) else "failed"
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        save()
        print("BLENDER_ADDON_GUI_PROBE " + json.dumps(report), flush=True)
        return None
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = repr(exc)
        try:
            addon.unregister()
        finally:
            save()
        return None


bpy.app.timers.register(advance, first_interval=0.1)
