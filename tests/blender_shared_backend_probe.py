"""Bounded installed-extension/public ToolSet integration in real Blender 5.1.

Run through an outer launcher with isolated BLENDER_USER_CONFIG/SCRIPTS/EXTENSIONS
and TEMP, a fresh --output, and a process timeout. Explicit local artifacts only:
no download, DCC server, source injection, worker, extra event loop, UI observation
or input. HTML uses the renderer's actual AuroraView SDK. This certifies the API
round trip and ownership, not keyboard/mouse delivery or visual acceptance.
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import importlib
import importlib.metadata
import io
import json
import os
import sys
import threading
import time
import traceback
import zipfile
from pathlib import Path

HTML = """<!doctype html><meta charset="utf-8"><title>Shared backend API probe</title>
<style>body{background:#17212b;color:#fff;font:20px sans-serif}</style>
<h1>Shared ToolSet API probe</h1><p id="result">Waiting for SDK</p><script>
const stage = __STAGE__;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function run() {
  const api = await window.auroraview.whenReady();
  const events = [];
  const remove = api.on('scene.changed', payload => events.push(payload));
  const result = {mutation_results: [], unsubscribe_type: typeof remove};
  try {
    if (stage === 'first') {
      result.mutation_results.push(await api.call('object.rename', {name:'SharedBackendVerified'}));
      result.mutation_results.push(await api.call('object.position', {location:[1.25,-2.5,3.75]}));
      result.mutation_results.push(await api.call('scene.frame', {frame:17}));
      result.before_invalid = await api.call('scene.snapshot', {});
      try {
        await api.call('scene.frame', {frame:'invalid'});
        result.invalid = {name:'unexpected_success'};
      } catch (error) {
        result.invalid = {name:error.name, message:error.message};
      }
    }
    result.snapshot = await api.call('scene.snapshot', {});
    await api.call('probe.emit', {stage});
    for (let n=0; events.length === 0 && n<100; ++n) await delay(20);
    remove();
    await api.call('probe.emit', {stage});
    await delay(100);
    result.events = events;
    await api.call('probe.report', {stage, payload:result});
    document.getElementById('result').textContent = 'SDK round trip complete: ' + stage;
  } catch (error) {
    remove();
    await api.call('probe.report', {stage, payload:{error:String(error), stack:error.stack || ''}});
  }
}
if (window.auroraview) run();
else window.addEventListener('auroraviewready', run, {once:true});
</script>"""


def object_schema(properties=None):
    properties = properties or {}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


class Probe:
    def __init__(self, bpy, args):
        self.bpy, self.args = bpy, args
        self.started = self.phase_started = time.monotonic()
        self.phase = "install"
        self.addon = self.tools = self.lease = self.target = None
        self.clients, self.managers, self.calls = [], [], []
        self.handlers, self.history, self.removals, self.results = {}, [], [], {}
        self.revision = 0
        self.surface_id = None
        self.report = {
            "status": "running",
            "pid": os.getpid(),
            "blender": bpy.app.version_string,
            "blender_build_hash": bpy.app.build_hash.decode("ascii"),
            "python": sys.version,
            "scope": "installed_public_toolset_sdk_bpy_api_not_interactive_acceptance",
            "ui_input_performed": False,
            "screenshots_taken": False,
            "checks": [],
        }
        atexit.register(self.verify_exit)

    def save(self):
        self.report.update(phase=self.phase, elapsed=time.monotonic() - self.started)
        self.args.output.write_text(json.dumps(self.report, indent=2) + "\n", encoding="utf-8")

    def check(self, name, condition):
        self.report["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)

    def next(self, phase):
        self.phase, self.phase_started = phase, time.monotonic()
        self.save()

    def owner_thread(self, operation):
        self.calls.append({"operation": operation, "thread_id": threading.get_ident()})
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Business API or event source escaped Blender's main thread")

    def snapshot(self):
        self.owner_thread("readback")
        return {
            "name": self.target.name,
            "location": list(self.target.location),
            "frame": self.bpy.context.scene.frame_current,
            "revision": self.revision,
        }

    def rename(self, name):
        self.owner_thread("rename")
        self.target.name = name
        self.revision += 1
        return {"name": self.target.name}

    def position(self, location):
        self.owner_thread("position")
        self.target.location = location
        self.revision += 1
        return {"location": list(self.target.location)}

    def frame(self, frame):
        self.owner_thread("frame")
        self.bpy.context.scene.frame_set(frame)
        self.revision += 1
        return {"frame": self.bpy.context.scene.frame_current}

    def subscribe(self, event, callback):
        self.owner_thread("subscribe")
        token = len(self.history)
        self.history.append(callback)
        self.handlers[token] = (event, callback)

        def unsubscribe():
            self.owner_thread("unsubscribe")
            self.handlers.pop(token, None)
            self.removals.append(token)
            # Public 0.1.0 requires synchronous cleanup that raises on failure.
            # Its returned consumer unsubscribe completes with None.

        return unsubscribe

    def emit(self, stage):
        self.owner_thread("emit")
        if stage == "second":
            self.history[0]({"token": "stale", "stage": stage, "revision": self.revision})
        for event, callback in tuple(self.handlers.values()):
            if event == "scene.changed":
                callback({"token": "current", "stage": stage, "revision": self.revision})
        return {"subscriber_count": len(self.handlers)}

    def receive(self, stage, payload):
        self.owner_thread("html_report")
        if stage in self.results:
            raise RuntimeError("Duplicate HTML completion report")
        self.results[stage] = payload
        return {"received": stage}

    def load_public_tools(self):
        self.check("public_package_not_preimported", "auroraview_dcc_mcp" not in sys.modules)
        # Keep Blender's standard library first; load the verified wheel before
        # its explicit dependency directory, never an adjacent source checkout.
        sys.path.extend([str(self.args.public_wheel), str(self.args.dependency_root)])
        public = importlib.import_module("auroraview_dcc_mcp")
        schema = importlib.import_module("jsonschema")
        self.report.update(public_package_origin=public.__file__, jsonschema_origin=schema.__file__)
        self.check(
            "public_package_loaded_directly_from_verified_wheel",
            public.__file__.startswith(str(self.args.public_wheel) + os.sep),
        )
        self.check(
            "real_jsonschema_from_explicit_dependency_root",
            Path(schema.__file__).resolve().is_relative_to(self.args.dependency_root),
        )
        versions = {
            name: importlib.metadata.version(name)
            for name in (
                "auroraview-dcc-mcp",
                "jsonschema",
                "attrs",
                "jsonschema-specifications",
                "referencing",
                "rpds-py",
            )
        }
        self.report["public_distribution_versions"] = versions
        self.check("published_toolset_version", versions["auroraview-dcc-mcp"] == "0.1.0")
        self.target = self.bpy.data.objects["Cube"]
        empty = object_schema()
        mutation = {"type": "object", "required": ["result", "scene"]}
        tools = [
            public.Tool(
                "scene.snapshot",
                "Read actual Blender state",
                empty,
                self.snapshot,
                output_schema={"type": "object"},
                read_only=True,
                destructive=False,
            )
        ]
        for name, description, properties, handler in (
            ("object.rename", "Rename the probe object", {"name": {"type": "string"}}, self.rename),
            (
                "object.position",
                "Position the probe object",
                {
                    "location": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                    }
                },
                self.position,
            ),
            ("scene.frame", "Set actual Blender frame", {"frame": {"type": "integer"}}, self.frame),
        ):
            tools.append(
                public.Tool(
                    name,
                    description,
                    object_schema(properties),
                    handler,
                    readback=self.snapshot,
                    output_schema=mutation,
                )
            )
        tools.extend(
            [
                public.Tool(
                    "probe.emit",
                    "Emit a synchronous host event",
                    object_schema({"stage": {"enum": ["first", "second"]}}),
                    self.emit,
                ),
                public.Tool(
                    "probe.report",
                    "Return actual SDK evidence",
                    object_schema(
                        {"stage": {"enum": ["first", "second"]}, "payload": {"type": "object"}}
                    ),
                    self.receive,
                ),
            ]
        )
        self.tools = public.ToolSet(
            "blender-shared-backend-probe", tools, dcc="blender", subscribe=self.subscribe
        )
        self.lease = self.tools.borrow()

    def install(self):
        bpy, isolation = self.bpy, self.args.isolation_root
        self.check("real_blender_51_gui", bpy.app.version[:2] == (5, 1) and not bpy.app.background)
        for suffix in ("CONFIG", "SCRIPTS", "EXTENSIONS"):
            path = Path(os.environ.get("BLENDER_USER_" + suffix, "")).resolve(strict=True)
            self.check("isolated_user_" + suffix.lower(), path.is_relative_to(isolation))
        temporary = isolation / "temporary"
        temporary.mkdir(exist_ok=True)
        bpy.context.preferences.filepaths.temporary_directory = str(temporary)
        self.check(
            "isolated_session_recovery_directory", Path(bpy.app.tempdir).is_relative_to(isolation)
        )
        for name, path in (
            ("public_wheel", self.args.public_wheel),
            ("extension", self.args.extension),
            ("bundle_manifest", self.args.renderer_bundle / "bundle-manifest.json"),
        ):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.report[name + "_sha256"] = digest
            self.check(
                name + "_matches_expected_sha256", digest == getattr(self.args, name + "_sha256")
            )
        self.load_public_tools()
        os.environ["AURORAVIEW_RENDERER_BUNDLE"] = str(self.args.renderer_bundle)
        repo_dir = isolation / "probe-repository"
        repo_dir.mkdir()
        self.check(
            "create_isolated_extension_repository",
            bpy.ops.preferences.extension_repo_add(
                name="Shared Backend Probe",
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
            "install_and_enable_verified_extension",
            bpy.ops.extensions.package_install_files(
                filepath=str(self.args.extension), repo=repo.module, enable_on_install=True
            )
            == {"FINISHED"},
        )
        self.addon = importlib.import_module(f"bl_ext.{repo.module}.auroraview_blender")
        client = importlib.import_module("auroraview_offscreen")
        self.report.update(addon_origin=self.addon.__file__, client_origin=client.__file__)
        self.check(
            "installed_extension_and_client_are_isolated",
            all(Path(module.__file__).is_relative_to(isolation) for module in (self.addon, client)),
        )
        with zipfile.ZipFile(self.args.extension) as archive:
            sources = [
                name
                for name in archive.namelist()
                if name.endswith(".py") or name.startswith("assets/")
            ]
            self.check(
                "installed_extension_matches_verified_zip",
                sources
                and all(
                    archive.read(name) == (Path(self.addon.__file__).parent / name).read_bytes()
                    for name in sources
                ),
            )
            wheels = [name for name in archive.namelist() if name.startswith("wheels/")]
            self.check("one_packaged_client_wheel", len(wheels) == 1)
            content = archive.read(wheels[0])
            self.report["client_wheel_sha256"] = hashlib.sha256(content).hexdigest()
            self.check(
                "client_wheel_matches_expected_sha256",
                self.report["client_wheel_sha256"] == self.args.client_wheel_sha256,
            )
            with zipfile.ZipFile(io.BytesIO(content)) as wheel:
                modules = [
                    name
                    for name in wheel.namelist()
                    if name.startswith("auroraview_offscreen/") and name.endswith(".py")
                ]
                self.check(
                    "installed_client_matches_verified_wheel",
                    modules
                    and all(
                        wheel.read(name)
                        == (
                            Path(client.__file__).parent
                            / Path(name).relative_to("auroraview_offscreen")
                        ).read_bytes()
                        for name in modules
                    ),
                )
        self.open("first")
        self.next("first")

    def open(self, stage):
        candidates = [
            (window, area)
            for window in self.bpy.context.window_manager.windows
            for area in window.screen.areas
            if area.type == "VIEW_3D"
        ]
        window, area = max(candidates, key=lambda item: item[1].width * item[1].height)
        with self.bpy.context.temp_override(window=window, area=area):
            self.surface_id = self.addon.open_editor(
                self.bpy.context,
                split=True,
                html=HTML.replace("__STAGE__", json.dumps(stage)),
                backend=self.lease,
                events=("scene.changed",),
            )
        manager = self.addon._docking.manager
        self.managers.append(manager)
        self.clients.append(manager.renderer)

    def ready(self, stage):
        manager = self.addon._docking.manager
        if manager is None or manager.last_error or self.addon._docking.last_error:
            raise RuntimeError("Native surface failed: " + str(self.addon._docking.last_error))
        info = manager.get_info(self.surface_id)
        if info["error"]:
            raise RuntimeError(str(info["error"]))
        return stage in self.results and info["frame_count"] >= 3 and info["uploaded_sequence"] >= 1

    def validate_html(self, stage):
        result = self.results[stage]
        self.report.setdefault("html_results", {})[stage] = result
        self.check(stage + "_sdk_completed", "error" not in result)
        expected = {
            "name": "SharedBackendVerified",
            "location": [1.25, -2.5, 3.75],
            "frame": 17,
            "revision": 3,
        }
        self.check(
            stage + "_sdk_and_actual_bpy_readback",
            result["snapshot"] == expected == self.snapshot(),
        )
        self.check(
            stage + "_event_roundtrip_and_js_unsubscribe",
            result["unsubscribe_type"] == "function"
            and result["events"] == [{"token": "current", "stage": stage, "revision": 3}],
        )
        if stage == "first":
            self.check(
                "sdk_mutations_each_have_actual_bpy_readback",
                [item["scene"]["revision"] for item in result["mutation_results"]] == [1, 2, 3],
            )
            self.check(
                "real_jsonschema_rejects_before_mutation",
                result["invalid"]["name"] == "ContractError"
                and result["before_invalid"] == expected
                and self.revision == 3,
            )

    def close_editor(self):
        try:
            outcome = self.bpy.ops.auroraview.close_editors()
        except RuntimeError as exc:
            if "AuroraView Blender cleanup is incomplete" not in str(exc):
                raise
            self.report["cleanup_retries"] = self.report.get("cleanup_retries", 0) + 1
            return False
        if outcome != {"FINISHED"}:
            raise RuntimeError("Native editor close failed: " + str(outcome))
        return self.addon._docking.manager is None

    def step(self):
        if self.phase == "install":
            self.install()
        elif self.phase in ("first", "second") and self.ready(self.phase):
            self.validate_html(self.phase)
            self.next(self.phase + "_close")
        elif self.phase in ("first_close", "second_close") and self.close_editor():
            stage = self.phase.removesuffix("_close")
            self.check(
                stage + "_close_unsubscribes_without_closing_caller",
                not self.handlers
                and not self.lease.closed
                and not self.tools.closed
                and self.lease.call("scene.snapshot")["revision"] == 3,
            )
            self.check(
                stage + "_close_releases_owned_renderer",
                self.clients[-1].closed and not self.managers[-1].needs_tick,
            )
            if stage == "first":
                self.open("second")
                self.next("second")
            else:
                self.check("both_host_subscriptions_disposed_once", self.removals == [0, 1])
                self.lease.close()
                self.check(
                    "caller_closes_lease_and_owner_stays_usable",
                    self.lease.closed
                    and not self.tools.closed
                    and self.tools.call("scene.snapshot")["revision"] == 3,
                )
                self.tools.close()
                self.check("original_caller_closes_tool_owner", self.tools.closed)
                self.report["business_calls"] = self.calls
                self.check(
                    "all_business_calls_readbacks_and_events_on_main_thread",
                    all(call["thread_id"] == threading.main_thread().ident for call in self.calls)
                    and threading.enumerate() == [threading.main_thread()],
                )
                self.addon.unregister()
                self.report["status"] = "awaiting_normal_exit"
                self.save()
                self.bpy.ops.wm.quit_blender()

    def advance(self):
        try:
            if time.monotonic() - self.started > self.args.timeout:
                raise TimeoutError("Shared backend probe expired: " + self.phase)
            if time.monotonic() - self.phase_started > 30:
                raise TimeoutError("Shared backend stage expired: " + self.phase)
            self.step()
        except Exception as exc:
            self.report.update(status="failed", error=repr(exc), traceback=traceback.format_exc())
            for owner in (self.addon, self.lease, self.tools):
                if owner is not None:
                    try:
                        owner.unregister() if owner is self.addon else owner.close()
                    except Exception as cleanup_error:
                        self.report.setdefault("cleanup_errors", []).append(repr(cleanup_error))
            self.save()
            self.bpy.ops.wm.quit_blender()
            return None
        return 0.1

    def verify_exit(self):
        # RNA can be invalid here: inspect retained Python owners only.
        clean = (
            all(client.closed for client in self.clients)
            and all(not manager.needs_tick for manager in self.managers)
            and not self.handlers
            and self.lease is not None
            and self.lease.closed
            and self.tools is not None
            and self.tools.closed
            and self.addon is not None
            and self.addon._session is None
            and self.addon._docking is None
        )
        self.report.update(
            exit_resources_cleaned=clean,
            owned_renderer_pids=[client.pid for client in self.clients],
        )
        if self.report["status"] == "awaiting_normal_exit":
            self.report["checks"].append(
                {"name": "normal_exit_retains_no_owned_resources", "passed": clean}
            )
            self.report["status"] = "passed" if clean else "failed"
        self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "extension",
        "renderer-bundle",
        "isolation-root",
        "output",
        "public-wheel",
        "dependency-root",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("extension", "public-wheel", "client-wheel", "bundle-manifest"):
        parser.add_argument("--" + name + "-sha256", required=True)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    if args.output.exists() or not 30 <= args.timeout <= 120:
        raise RuntimeError("Use fresh output and a timeout between 30 and 120 seconds")
    for name in (
        "extension",
        "renderer_bundle",
        "isolation_root",
        "public_wheel",
        "dependency_root",
    ):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    import bpy

    probe = Probe(bpy, args)
    bpy.app.timers.register(probe.advance, first_interval=1.0, persistent=True)


if __name__ == "__main__":
    main()
