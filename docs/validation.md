# Validation and remaining gates

The initial adapter import matched independently reviewed source
79425d579c127324b797113f6ffd985351c52569. Later commits add consumer configuration,
native panels, extension packaging and registration-generation cancellation.
Historical host evidence below applies to its original candidate. Original MIT
notices are retained.

## Native tools and extension validation

On 2026-10-08, Blender 4.2.3 LTS on Windows accepted the generated extension ZIP
through its official `extension validate` command. An isolated real GUI then
installed and enabled that ZIP through Blender's extension operator. Thirteen
checks passed for extension namespace isolation, native selection/consumer panel
registration, idempotent registration, unregister, module reload and an actual
save/open file cycle. File load restarted the timer and rejected the prior
generation's dispatcher. Core was never imported and no WebView was created.

The first GUI probe timed out waiting for UI acceptance. DCC-CUA 1.9.1 later
opened the native sidebar after an explicit session-state refresh. The built-in
selection/transform controls and independent consumer panel were visible, and
the installed add-on verified its draw callback ran on the main thread. Native
property-edit acceptance failed: background text delivery did not change the
object name and foreground delivery returned `foreground_unavailable`. The
readback reported the unchanged `Cube` and failed that check; no UI-edit pass is
claimed. No generic computer-use provider was substituted.

## Actual Blender host check

On 2026-10-05, a dedicated real Blender 4.3.2 GUI with CPython 3.13.5 passed all
21 checks in tests/blender_gui_probe.py. Its SHA256 was
f2164d77671d569e0a356d8c6165fd2af1e493f62d6e9935317821c9baaf04b6.

The checks covered GUI mode, idempotent registration, actual bpy timer ownership,
load-handler/operator/panel registration, a worker-submitted context query on
the actual main thread, explicit Linux rejection, no Core import, unregistering
the timer/panel/handlers, discarded queued work, a fresh re-enabled session and
final cleanup. The visible sidebar showed its unsupported-platform warning and
disabled WebView button. Blender exited normally with code 0, with no forced
termination. All probe-owned windows were closed.

This probe did not import Core or create a native WebView. It establishes host
registration and dispatch behavior only. A separate official Core 0.5.11 native
module import passed using isolated official dependencies; that also created no
WebView and did not validate the proposed Core Python wrapper.

## Source checks

- 55 host/Core-double, native-panel and packaging unit tests passed
- Ruff lint and formatting passed for the adapter and tests
- The reviewed Core R4 candidate passed 224 targeted Python regressions,
  including eight independent failure reproductions
- Independent review repeated six Core close/reentrancy barrier cases five times
- The follow-up Core event/RPC/lifecycle suite passed 97 targeted regressions;
  notification dispatch and veto refusal are source-tested, not native WebView QA

The [final Core CI](https://github.com/try-auroraview/auroraview/actions/runs/37677003305)
also ran required tests inside actual Blender 3.6.21: four collected, four passed,
zero skipped. The final Python CI executed all 97 focused contract regressions.
These host/source checks do not establish visible WebView rendering or native
window lifecycle acceptance.

The public CI checks unit behavior, style, sdist/wheel creation, source equality
inside the wheel and lazy import without Blender/Core. It does not launch Blender.
Mocked tests are not renderer or native lifecycle evidence.

## Native acceptance still required

- Real WebView creation, visible HTML and bridge readiness
- JS-to-Python RPC executing on Blender's main thread and returning results/errors
- Native close/reopen, two independent views, repeated and interrupted closure
- Add-on disable/re-enable, module reload, file load and host exit with live views
- Native callback/GC release and WebView helper-process termination
- Exact supported host, OS, architecture and available Core build declaration

Windows WebView rendering is a source candidate only. Linux/macOS WebView launch
remains explicitly unavailable. Native panels render Blender controls; they do
not embed HTML. WebView support claims must wait for these acceptance results.
