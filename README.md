# AuroraView Blender

Experimental Blender host adapter for [AuroraView Core](https://github.com/try-auroraview/auroraview). This is a development source candidate, not a native WebView-certified release.

The adapter owns Blender's main-thread queue, sidebar launcher, operator registration and host cleanup. Core owns the native WebView, JavaScript bridge, RPC envelopes and window lifecycle. No private renderer or bridge is bundled.

## Verified scope

- **Actual Blender 4.3.2 / CPython 3.13.5 on Linux:** 21 addon-host checks passed, including worker-to-main-thread dispatch, registration, one disable/re-enable cycle and cleanup; Blender exited normally with code 0
- **Host unit tests:** 17 passed using host/Core doubles
- **Native WebView, JS/Python RPC and window lifecycle:** not tested in a real host
- **Windows floating-window route:** source candidate; native GUI acceptance pending
- **Linux/macOS WebView route:** explicitly unavailable; the sidebar button stays disabled
- **Blender panel embedding:** not implemented; the sidebar is a launcher

The real host probe did not import Core or construct a WebView. See [validation](docs/validation.md) for evidence and limits.

## Core dependency

This candidate requires the API contract proposed in [AuroraView Core PR #497](https://github.com/try-auroraview/auroraview/pull/497), at [source commit 7bfb453](https://github.com/try-auroraview/auroraview/commit/7bfb45376c4428c6d3f36fc9361eca436e379d97).

**No released Core dependency version is declared compatible.** The PR is a draft and must pass its own CI/review. Its source version is based on 0.5.11; installing the released auroraview==0.5.11 wheel does not supply these changes. This package deliberately does not download Core automatically. Use an explicitly reviewed, compatible build when one becomes available. See the [exact contract](docs/core-compatibility.md).

The separate experimental Linux GTK Core work is not included or enabled here.

## Source development

Use src/ on Blender's Python path, then:

```python
import auroraview_blender

auroraview_blender.register()
# View3D > Sidebar > AuroraView

auroraview_blender.unregister()
```

The default RPC reads selection metadata. There is no arbitrary code-execution RPC, external listener or implicit dependency installation. Calls use one bounded Blender timer queue. File-load hooks stop the old session and start a fresh queue; module reload disposes the prior registration. Native-window cleanup during these flows still needs live WebView validation.

## Validation commands

```sh
python -m pip install build==1.2.2 hatchling==1.27.0 ruff==0.16.10
python -m unittest discover -s tests -v
python -m ruff check src tests tools
python -m ruff format --check src tests tools
python -m build --no-isolation
python tools/verify_wheel.py
```

CI runs these source/package checks across Python 3.10–3.13. It does not launch Blender or certify native WebView support, and does not publish packages or releases.

tests/blender_gui_probe.py is a separate real Linux Blender addon-host probe. Start a dedicated GUI session with --factory-startup --python <probe>, then pass --output <new-report.json> and --cleanup-marker <new-marker> after Blender's -- separator. Inspect the AuroraView sidebar when the report says awaiting_visual_check, create the marker to request cleanup, verify the report, then quit only that session normally. The probe has a 180-second observation limit and never creates a WebView.

## Release gate

Version 0.1.0.dev0 identifies development source. Native rendering/RPC, close/reopen, multiple windows, reload/file-load cleanup and helper-process termination must be tested against an available Core build before any native support claim. A stable release is not ready.

## License

MIT; see [LICENSE](LICENSE).
