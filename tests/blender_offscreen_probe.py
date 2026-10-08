"""Bounded native extension lifecycle probe, without UI input or screenshots.

Run in its own Blender 5.1 GUI process with --factory-startup. All three
BLENDER_USER_CONFIG/SCRIPTS/EXTENSIONS directories must be children of the
explicit --isolation-root. Pass a locally built extension ZIP containing the
optional wheel and a verified renderer bundle; no source sys.path injection,
package download, listener, worker thread, or interaction with another Blender.

This script exercises native bpy APIs and real GPU uploads. It does not certify
mouse/keyboard behavior or visual acceptance. A launcher must additionally bound
the process lifetime, verify normal process exit, and read the final JSON status.
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import importlib
import io
import json
import os
import sys
import threading
import time
import traceback
import zipfile
from pathlib import Path

import tomllib

HTML = """<!doctype html><meta charset="utf-8"><title>Lifecycle probe</title>
<style>body{background:#17212b;color:#fff;font:20px sans-serif}</style>
<h1>Native extension lifecycle</h1><p id="tick"></p><script>
let frame=0; setInterval(()=>document.getElementById('tick').textContent=++frame,80);
</script>"""


class Probe:
    def __init__(self, bpy, args):
        self.bpy, self.args = bpy, args
        self.started = time.monotonic()
        self.phase = "install"
        self.phase_started = self.started
        self.addon = None
        self.clients = []
        self.managers = []
        self.ids = []
        self.previous = None
        self.report = {
            "pid": os.getpid(),
            "blender": bpy.app.version_string,
            "blender_build_hash": bpy.app.build_hash.decode("ascii"),
            "python": sys.version,
            "background": bpy.app.background,
            "scope": "native_extension_lifecycle_not_interactive_acceptance",
            "ui_input_performed": False,
            "screenshots_taken": False,
            "checks": [],
            "samples": [],
            "status": "running",
        }
        # Registered first: the extension's own later atexit handler runs first.
        atexit.register(self.verify_exit)

    def save(self):
        self.report["phase"] = self.phase
        self.report["elapsed"] = time.monotonic() - self.started
        self.args.output.write_text(json.dumps(self.report, indent=2) + "\n", encoding="utf-8")

    def check(self, name, passed):
        self.report["checks"].append({"name": name, "passed": bool(passed)})
        if not passed:
            raise AssertionError(name)

    def next(self, phase):
        self.phase, self.phase_started = phase, time.monotonic()
        self.save()

    def retry_cleanup(self, action):
        try:
            action()
        except RuntimeError as exc:
            if "AuroraView Blender cleanup is incomplete" not in str(exc):
                raise
            retries = self.report.setdefault("cleanup_retries", {})
            retries[self.phase] = retries.get(self.phase, 0) + 1
            self.save()
            return False
        return True

    def manager(self):
        return self.addon._docking.manager

    def track(self):
        manager = self.manager()
        if not any(item is manager for item in self.managers):
            self.managers.append(manager)
        if manager.renderer is not None and not any(
            item is manager.renderer for item in self.clients
        ):
            self.clients.append(manager.renderer)
        return manager

    def window_area(self, pointer=None):
        candidates = []
        for window in self.bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if pointer is not None and area.as_pointer() == pointer:
                    return window, area
                if pointer is None and area.type == "VIEW_3D":
                    candidates.append((window, area))
        if candidates:
            return max(candidates, key=lambda item: item[1].width * item[1].height)
        raise RuntimeError("Probe's native editor area no longer exists")

    def open(self):
        window, area = self.window_area()
        with self.bpy.context.temp_override(window=window, area=area):
            surface_id = self.addon.open_editor(self.bpy.context, split=True, html=HTML)
        self.track()
        return surface_id

    def frames_ready(self, ids, minimum=3):
        manager = self.manager()
        if manager is None or manager.last_error or self.addon._docking.last_error:
            raise RuntimeError(
                "Native surface failed: "
                + str(manager.last_error if manager else self.addon._docking.last_error)
            )
        infos = [manager.get_info(surface_id) for surface_id in ids]
        if any(info["error"] for info in infos):
            raise RuntimeError(str(infos))
        return all(
            info["frame_count"] >= minimum and info["uploaded_sequence"] >= 1 for info in infos
        )

    def sample(self, name):
        manager = self.manager()
        self.report["samples"].append(
            {
                "name": name,
                "renderer_pid": manager.renderer.pid if manager and manager.renderer else None,
                "surfaces": []
                if manager is None
                else [manager.get_info(surface_id) for surface_id in manager.surfaces],
                "modal_operators": self.modal_operators(),
            }
        )

    def modal_operators(self):
        return [
            {
                "window_pointer": window.as_pointer(),
                "operators": None
                if not hasattr(window, "modal_operators")
                else [getattr(item, "bl_idname", None) for item in window.modal_operators],
            }
            for window in self.bpy.context.window_manager.windows
        ]

    def install(self):
        bpy = self.bpy
        self.check("real_blender_51_gui", bpy.app.version[:2] == (5, 1) and not bpy.app.background)
        self.check("offscreen_not_preimported", "auroraview_offscreen" not in sys.modules)
        isolation = self.args.isolation_root.resolve(strict=True)
        for suffix in ("CONFIG", "SCRIPTS", "EXTENSIONS"):
            path = Path(os.environ.get("BLENDER_USER_" + suffix, "")).resolve(strict=True)
            self.check("isolated_user_" + suffix.lower(), path.is_relative_to(isolation))
        temporary = isolation / "temporary"
        temporary.mkdir()
        bpy.context.preferences.filepaths.temporary_directory = str(temporary)
        self.report["temporary_directory"] = bpy.app.tempdir
        self.check(
            "isolated_session_recovery_directory", Path(bpy.app.tempdir).is_relative_to(isolation)
        )
        os.environ["AURORAVIEW_RENDERER_BUNDLE"] = str(self.args.renderer_bundle)
        self.report["extension_sha256"] = hashlib.sha256(
            self.args.extension.read_bytes()
        ).hexdigest()
        self.report["bundle_manifest_sha256"] = hashlib.sha256(
            (self.args.renderer_bundle / "bundle-manifest.json").read_bytes()
        ).hexdigest()
        repo_dir = isolation / "probe-repository"
        repo_dir.mkdir()
        self.check(
            "create_isolated_extension_repository",
            bpy.ops.preferences.extension_repo_add(
                name="AuroraView Lifecycle Probe",
                type="LOCAL",
                use_custom_directory=True,
                custom_directory=str(repo_dir),
            )
            == {"FINISHED"},
        )
        repo = next(
            item
            for item in bpy.context.preferences.extensions.repos
            if Path(item.directory).resolve() == repo_dir
        )
        self.check(
            "install_and_enable_actual_extension_zip",
            bpy.ops.extensions.package_install_files(
                filepath=str(self.args.extension), repo=repo.module, enable_on_install=True
            )
            == {"FINISHED"},
        )
        self.addon = importlib.import_module(f"bl_ext.{repo.module}.auroraview_blender")
        self.report["addon_namespace"] = self.addon.__name__
        self.report["addon_origin"] = self.addon.__file__
        client = importlib.import_module("auroraview_offscreen")
        self.report["client_origin"] = client.__file__
        self.check(
            "optional_wheel_imported_from_isolation",
            Path(client.__file__).is_relative_to(isolation),
        )
        with zipfile.ZipFile(self.args.extension) as archive:
            self.report["installed_extension_version"] = tomllib.loads(
                archive.read("blender_manifest.toml").decode("utf-8")
            )["version"]
            source_files = [
                name
                for name in archive.namelist()
                if name.endswith(".py") or name.startswith("assets/")
            ]
            self.check(
                "installed_extension_matches_packaged_source",
                bool(source_files)
                and all(
                    archive.read(name) == (Path(self.addon.__file__).parent / name).read_bytes()
                    for name in source_files
                ),
            )
            wheels = [name for name in archive.namelist() if name.startswith("wheels/")]
            self.check("exactly_one_optional_wheel", len(wheels) == 1)
            content = archive.read(wheels[0])
            self.report["wheel_sha256"] = hashlib.sha256(content).hexdigest()
            with zipfile.ZipFile(io.BytesIO(content)) as wheel:
                modules = [
                    name
                    for name in wheel.namelist()
                    if name.startswith("auroraview_offscreen/") and name.endswith(".py")
                ]
                self.report["installed_client_modules"] = modules
                self.check(
                    "installed_client_matches_packaged_wheel",
                    bool(modules)
                    and all(
                        wheel.read(name)
                        == (
                            Path(client.__file__).parent
                            / Path(name).relative_to("auroraview_offscreen")
                        ).read_bytes()
                        for name in modules
                    ),
                )
        self.check(
            "extension_namespace_without_source_alias", "auroraview_blender" not in sys.modules
        )
        self.check("independent_of_core_native_module", "auroraview" not in sys.modules)
        self.check("extension_enabled", self.addon.__name__ in bpy.context.preferences.addons)
        first = self.addon._session
        self.addon.register()
        self.check("idempotent_register", self.addon._session is first)
        self.report["ui_scale"] = bpy.context.preferences.system.ui_scale
        self.ids = [self.open(), self.open()]
        self.next("multi")

    def advance(self):
        try:
            if threading.current_thread() is not threading.main_thread():
                raise RuntimeError("Probe must run on Blender's owner thread")
            if time.monotonic() - self.started > self.args.timeout:
                raise TimeoutError("Bounded native lifecycle probe expired in " + self.phase)
            if time.monotonic() - self.phase_started > 25:
                raise TimeoutError("Native lifecycle stage expired: " + self.phase)
            self.step()
        except Exception as exc:
            self.report.update(status="failed", error=repr(exc), traceback=traceback.format_exc())
            if self.addon is not None:
                try:
                    self.addon.unregister()
                except Exception as cleanup_error:
                    self.report["cleanup_error"] = repr(cleanup_error)
            self.save()
            self.bpy.ops.wm.quit_blender()
            return None
        return 0.1

    def step(self):
        bpy = self.bpy
        if self.phase == "install":
            self.install()
        elif self.phase == "multi" and self.frames_ready(self.ids):
            manager = self.manager()
            self.check(
                "two_surfaces_share_one_owned_renderer",
                len(manager.surfaces) == 2 and len(self.clients) == 1,
            )
            self.sample("multi_surface_gpu_upload")
            self.previous = manager.get_info(self.ids[0])
            window, area = self.window_area(self.previous["area_pointer"])
            with bpy.context.temp_override(window=window, area=area):
                self.check(
                    "native_resize_split_completed",
                    bpy.ops.screen.area_split("EXEC_DEFAULT", direction="HORIZONTAL", factor=0.65)
                    == {"FINISHED"},
                )
            self.next("resize")
        elif self.phase == "resize":
            info = self.manager().get_info(self.ids[0])
            if (
                (info["width"], info["height"]) != (self.previous["width"], self.previous["height"])
                and info["frame_count"] > self.previous["frame_count"]
                and info["uploaded_sequence"] > self.previous["uploaded_sequence"]
            ):
                self.check("resized_surface_has_fresh_gpu_upload", True)
                self.sample("native_resize")
                self.previous = self.manager().get_info(self.ids[1])
                self.manager().close(self.ids[0])
                self.next("survivor")
        elif self.phase == "survivor":
            info = self.manager().get_info(self.ids[1])
            if info["frame_count"] > self.previous["frame_count"]:
                self.check(
                    "closing_one_surface_preserves_other_renderer",
                    self.manager().renderer is self.clients[0],
                )
                self.sample("survivor_after_close")
                self.manager().close(self.ids[1])
                self.next("reap")
        elif self.phase == "reap" and (self.manager() is None or not self.manager().needs_tick):
            self.check("last_surface_close_reaps_owned_renderer", self.clients[0].closed)
            self.ids = [self.open()]
            self.next("reopen")
        elif self.phase == "reopen" and self.frames_ready(self.ids):
            self.check(
                "close_then_reopen_starts_fresh_owned_renderer",
                len(self.clients) == 2 and self.clients[1] is not self.clients[0],
            )
            self.sample("reopen")
            manager, client = self.manager(), self.manager().renderer
            self.previous = (manager, client)
            # Cancel this probe's renderer through its public owned-process API.
            # The host must notice the exit, release native resources and allow
            # the next open, rather than retaining a dead surface generation.
            cancel_started = time.monotonic()
            client.terminate(timeout=3.0)
            self.report["renderer_cancel_elapsed"] = time.monotonic() - cancel_started
            self.check("owned_renderer_cancel_completes", client.closed)
            self.next("cancel_cleanup")
        elif self.phase == "cancel_cleanup" and self.manager() is None:
            manager, client = self.previous
            self.check(
                "host_timer_releases_cancelled_renderer_generation",
                client.closed and not manager.needs_tick and not manager.surfaces,
            )
            self.ids = [self.open()]
            self.next("after_cancel")
        elif self.phase == "after_cancel" and self.frames_ready(self.ids):
            self.check(
                "open_after_cancel_starts_fresh_owned_renderer",
                len(self.clients) == 3 and self.clients[2] is not self.clients[1],
            )
            self.sample("reopen_after_cancel")
            self.previous = (
                self.manager(),
                self.manager().renderer,
                self.addon._session.scheduler.dispatcher(),
            )
            file_path = self.args.isolation_root / "lifecycle.blend"
            self.next("load")
            bpy.ops.wm.save_as_mainfile(filepath=str(file_path))
            bpy.ops.wm.open_mainfile(filepath=str(file_path))
        elif self.phase == "load":
            manager, client, dispatch = self.previous
            self.check(
                "file_load_disposes_surfaces_and_renderer", not manager.needs_tick and client.closed
            )
            self.check("file_load_restarts_host_scheduler", self.addon._session.scheduler.running)
            try:
                dispatch(lambda: None)
            except RuntimeError as exc:
                self.check("file_load_expires_old_dispatcher", "expired" in str(exc))
            else:
                self.check("file_load_expires_old_dispatcher", False)
            self.ids = [self.open()]
            self.next("unregister")
        elif self.phase == "unregister" and self.frames_ready(self.ids):
            self.sample("after_file_load")
            manager, client = self.manager(), self.manager().renderer
            session = self.addon._session
            handlers = tuple(self.addon._handlers)
            self.previous = (manager, client, session, handlers)
            self.next("unregister_cleanup")
        elif self.phase == "unregister_cleanup":
            if not self.retry_cleanup(self.addon.unregister):
                return
            manager, client, session, handlers = self.previous
            self.check(
                "unregister_releases_native_surfaces_and_process",
                not manager.needs_tick and client.closed,
            )
            self.check(
                "unregister_stops_scheduler_and_handlers",
                not session.scheduler.running
                and all(handler not in collection for collection, handler in handlers),
            )
            self.check(
                "unregister_removes_owned_classes",
                not hasattr(bpy.types, "AURORAVIEW_PT_selection")
                and not hasattr(bpy.types, "AURORAVIEW_OT_surface_input"),
            )
            self.report["modal_after_unregister"] = self.modal_operators()
            self.next("unregister_modal")
        elif self.phase == "unregister_modal" and time.monotonic() - self.phase_started >= 0.3:
            self.report["modal_after_unregister_wait"] = self.modal_operators()
            importlib.reload(self.addon)
            self.addon.register()
            self.ids = [self.open()]
            self.next("reload")
        elif self.phase == "reload" and self.frames_ready(self.ids):
            self.sample("after_unregister_and_register")
            manager, client, session = self.manager(), self.manager().renderer, self.addon._session
            self.previous = (manager, client, session)
            self.next("reload_cleanup")
        elif self.phase == "reload_cleanup":
            if not self.retry_cleanup(lambda: importlib.reload(self.addon)):
                return
            manager, client, session = self.previous
            self.check(
                "active_module_reload_disposes_previous_generation",
                not manager.needs_tick
                and client.closed
                and not session.scheduler.running
                and self.addon._session is None,
            )
            self.addon.register()
            self.ids = [self.open()]
            self.next("exit")
        elif self.phase == "exit" and self.frames_ready(self.ids):
            self.sample("active_resources_before_normal_exit")
            self.check(
                "python_work_stayed_on_main_thread",
                threading.enumerate() == [threading.main_thread()],
            )
            self.report["status"] = "awaiting_owned_exit_cleanup"
            self.save()
            # Leave active resources deliberately: the extension owns normal exit.
            bpy.ops.wm.quit_blender()

    def verify_exit(self):
        # Blender RNA may already be gone here; inspect only retained Python owners.
        try:
            clean = all(client.closed for client in self.clients) and all(
                not manager.needs_tick for manager in self.managers
            )
            if self.addon is not None:
                clean = clean and (
                    self.addon._session is None
                    and self.addon._docking is None
                    and self.addon._panels is None
                    and not self.addon._classes
                    and not self.addon._handlers
                )
            self.report["exit_resources_cleaned"] = clean
            self.report["owned_renderer_pids"] = [client.pid for client in self.clients]
            if self.report["status"] == "awaiting_owned_exit_cleanup":
                self.check("normal_exit_cleans_all_owned_renderers_and_surfaces", clean)
                self.report["status"] = "passed"
        except Exception as exc:
            self.report.update(status="failed", exit_error=repr(exc))
        self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension", type=Path, required=True)
    parser.add_argument("--renderer-bundle", type=Path, required=True)
    parser.add_argument("--isolation-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    if args.output.exists() or not 30 <= args.timeout <= 300:
        raise RuntimeError("Use fresh evidence and a timeout between 30 and 300 seconds")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.extension = args.extension.resolve(strict=True)
    args.renderer_bundle = args.renderer_bundle.resolve(strict=True)
    args.isolation_root = args.isolation_root.resolve(strict=True)
    import bpy

    probe = Probe(bpy, args)
    bpy.app.timers.register(probe.advance, first_interval=1.0, persistent=True)


if __name__ == "__main__":
    main()
