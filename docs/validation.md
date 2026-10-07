# Validation and remaining gates

The production adapter and original tests match independently reviewed source
79425d579c127324b797113f6ffd985351c52569. Publication documentation and packaging
checks do not change those runtime bytes. Original MIT notices are retained.

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

- 17 host/Core-double unit tests passed
- Ruff lint and formatting passed for the adapter and tests
- The reviewed Core R4 candidate passed 224 targeted Python regressions,
  including eight independent failure reproductions
- Independent review repeated six Core close/reentrancy barrier cases five times

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

Windows is a source candidate only. Linux/macOS WebView launch remains explicitly
unavailable. Sidebar registration is not native panel embedding. Stable/native
support claims must wait for these acceptance results.
