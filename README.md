# AuroraView Blender

Experimental Blender host adapter for [AuroraView Core](https://github.com/try-auroraview/auroraview). This is a development source candidate, not a native WebView-certified release.

The adapter owns Blender's native tool panels, main-thread queue, operator registration and host cleanup. Core owns the native WebView, JavaScript bridge, RPC envelopes and window lifecycle. No private renderer or bridge is bundled.

## Native tools

Install the Blender 4.2+ extension ZIP built by `python tools/build_extension.py`.
View3D > Sidebar > AuroraView includes a native Selection panel with editable
object name and transforms. Third-party add-ons can register native tools through
`register_panel` / `unregister_panel`. This route uses Blender's own controls and
requires no Core installation. See [installation and panel API](docs/native-panels.md).

## Verified scope

- **Baseline actual Blender 4.3.2 / CPython 3.13.5 on Linux:** 21 addon-host checks passed, including worker-to-main-thread dispatch, registration, one disable/re-enable cycle and cleanup; Blender exited normally with code 0. The consumer-hook follow-up has not been rerun in Blender
- **Source unit tests:** CI runs scheduler, lifecycle, native-panel and packaging checks with explicit host/Core doubles
- **Native WebView, JS/Python RPC and window lifecycle:** not tested in a real host
- **Windows floating-window route:** source candidate; native GUI acceptance pending
- **Linux/macOS WebView route:** explicitly unavailable; the sidebar button stays disabled
- **Native Blender tool panels:** implemented through the add-on API; HTML embedding remains unimplemented

The real host probe did not import Core or construct a WebView. See [validation](docs/validation.md) for evidence and limits.

## Core dependency

Optional WebView tools require the API contract in [AuroraView Core PR #497](https://github.com/try-auroraview/auroraview/pull/497), at [source commit 3c51bbf](https://github.com/try-auroraview/auroraview/commit/3c51bbf6700af636c93458a301efe2cc905c624e).

**No released Core dependency version is declared compatible.** Its source version is based on 0.5.11; installing the released auroraview==0.5.11 wheel does not supply these changes. This dependency applies only to optional WebView tools. Native panels work without Core. See the [exact contract](docs/core-compatibility.md).

The separate experimental Linux GTK Core work is not included or enabled here.

## Source development

Use src/ on Blender's Python path, then:

```python
import auroraview_blender

auroraview_blender.register()
# View3D > Sidebar > AuroraView

auroraview_blender.unregister()
```

The default RPC reads selection metadata. There is no arbitrary code-execution RPC, external listener or implicit dependency installation. Calls and event notifications use one bounded Blender timer queue. Synchronous closing-veto callbacks are rejected by the async event contract. File-load hooks discard pending work and expire old dispatchers before restarting; module reload disposes the prior registration. Failed unregister cleanup retains ownership for retry. Native-window cleanup during these flows still needs live WebView validation.

Third-party add-ons can own a `BlenderSession` and pass `configure(view)` to
`open` to bind public Core commands/events before show. See the
[consumer example and ownership rules](docs/consumer-tools.md). The hook and
reentrant-finalizer cleanup have source-only tests; they add no native support
claim and do not enable the separate GTK runtime.

## Validation commands

```sh
python -m pip install build==1.2.2 hatchling==1.27.0 ruff==0.16.10 tomli==2.2.1
python -m unittest discover -s tests -v
python -m ruff check src tests tools
python -m ruff format --check src tests tools
python -m build --no-isolation
python tools/verify_wheel.py
python tools/build_extension.py
python tools/verify_extension.py
```

CI runs these source/package checks across Python 3.10–3.13. It does not launch Blender or certify native WebView support, and does not publish packages or releases.

tests/blender_gui_probe.py is a separate real Linux Blender addon-host probe. Start a dedicated GUI session with --factory-startup --python <probe>, then pass --output <new-report.json> and --cleanup-marker <new-marker> after Blender's -- separator. Inspect the AuroraView sidebar when the report says awaiting_visual_check, create the marker to request cleanup, verify the report, then quit only that session normally. The probe has a 180-second observation limit and never creates a WebView.

`tests/blender_native_probe.py` installs the built ZIP into a fresh local extension
repository and verifies registration, module reload, actual file-load cancellation,
native panel drawing and cleanup. Run it in an isolated GUI with separate
`BLENDER_USER_CONFIG`, `BLENDER_USER_SCRIPTS` and `BLENDER_USER_EXTENSIONS` paths;
pass `--extension`, `--output` and `--cleanup-marker` after `--`. Observe the native
panel and optionally edit its object name (`--expected-name` enables readback),
then create the cleanup marker. The probe exits Blender normally after verification.

## Release gate

Version 0.1.0.dev0 identifies development source. Native rendering/RPC, close/reopen, multiple windows, reload/file-load cleanup and helper-process termination must be tested against an available Core build before any native support claim. A stable release is not ready.

## License

MIT; see [LICENSE](LICENSE).
