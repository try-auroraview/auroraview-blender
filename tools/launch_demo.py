"""Launch inside Blender via --python; no package install or external listener."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import bpy

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--renderer-bundle", type=Path, required=True)
parser.add_argument(
    "--client", type=Path, required=True, help="Pure Python wheel or source directory"
)
parser.add_argument("--evidence", type=Path)
parser.add_argument("--wait-for-open", action="store_true", help="Wait for native UI open action")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(args.client.resolve(strict=True)))
os.environ["AURORAVIEW_RENDERER_BUNDLE"] = str(args.renderer_bundle.resolve(strict=True))

import auroraview_blender as addon  # noqa: E402

bpy.context.preferences.view.show_splash = False
addon.register()
started = time.monotonic()
opened = False


def record():
    global opened
    context = bpy.context
    if not args.wait_for_open and not opened:
        window = context.window_manager.windows[0]
        area = next(area for area in window.screen.areas if area.type == "VIEW_3D")
        with context.temp_override(window=window, area=area):
            addon.open_editor(context)
        opened = True
    if args.evidence:
        docking = addon._docking
        manager = docking.manager if docking else None
        report = {
            "pid": os.getpid(),
            "blender": bpy.app.version_string,
            "python": sys.version,
            "background": bpy.app.background,
            "elapsed": time.monotonic() - started,
            "ui_scale": context.preferences.system.ui_scale,
            "pixel_size": context.preferences.system.pixel_size,
            "dpi": context.preferences.system.dpi,
            "surfaces": [],
            "scene": addon.get_agent_adapter().get_context(),
            "last_error": docking.last_error if docking else None,
            "window_modal_operators": [
                {
                    "window_pointer": window.as_pointer(),
                    "operators": None
                    if not hasattr(window, "modal_operators")
                    else [operator.bl_idname for operator in window.modal_operators][:32],
                }
                for window in list(context.window_manager.windows)[:16]
            ],
        }
        if manager:
            report["surfaces"] = [manager.get_info(key) for key in manager.surfaces]
            report["renderer_pid"] = manager.renderer.pid if manager.renderer else None
            report["renderer_ready"] = bool(manager.renderer and manager.renderer.ready)
            report["last_closed_info"] = manager.last_closed_info
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(report, indent=2), encoding="utf-8")
        # A task-owned marker requests cleanup; never close another Blender.
        if args.evidence.with_suffix(".stop").exists():
            addon.unregister()
            bpy.ops.wm.quit_blender()
            return None
    return 0.25


bpy.app.timers.register(record, first_interval=0.75)
