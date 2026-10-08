# Validation and remaining gates

Evidence is scoped to the tested candidate, host and operation. Source CI, native
rendering, input delivery and user acceptance are separate gates. The native Web
route is experimental; it is not a released or user-accepted integration.

## Native Web editor candidate

On 2026-10-09, an isolated Windows x64 Blender 5.1.1 GUI with Python 3.13.9
installed and enabled the final extension through Blender's extension operator.
The independent `tests/blender_offscreen_probe.py` passed 36 actual-host checks:
exact installed source and wheel bytes, two native areas with GPU frame uploads,
resizing, independent area closure, final-area process reaping, fresh reopening,
renderer cancellation/reopening, file loading with dispatcher invalidation,
unregister/re-register, module reload and active host exit. All six owned renderer
processes and the probe Blender exited; its externally retained exit code was 0.
The probe and its recovery files used isolated temporary/configuration directories.

The final candidate uses the public persistent `bpy.app.handlers.exit_pre` hook
on hosts that expose it, before Blender tears down native data. A following
unrelated exit handler verified main-thread cleanup and empty add-on owners. It
was not skipped by mutation of the live handler list. Earlier active-exit runs
reported 55 native blocks (approximately 4 KiB), absent from a no-add-on baseline;
the final early-cleanup run had no allocator warning or cleanup error in either
stdout or stderr. Older hosts retain an `atexit` fallback. This evidence applies
to the tested Windows host and candidate, not every supported sidebar host.

These probes run on Blender's main thread. They establish native region drawing,
scene RPC dispatch and ownership behavior; they do not send keyboard or mouse
input and do not establish human acceptance. A process disappearing by itself
is insufficient evidence of clean exit.

DCC-CUA 1.9.4 captured the final standalone demo, precisely bound to Blender
PID 59488 and HWND 104077806. Blender's UI scale was 2.5; the 1569 by 1878 pixel
region mapped to a 627 by 751 logical Web surface. A Web click selected Cube,
filled the name field and advanced the host scene revision from 0 to 1. The
before/after foreground identity remained the exact target; both pixels and
structured scene readback verified the effect. The demo's normal stop reported
complete cleanup; Blender and its renderer exited, with no cleanup error or
allocator warning in the saved logs. Its recovery file remained isolated.

Subsequent focus attempts were refused by the exact-window foreground activation
route, before any keyboard input. A transient UIA snapshot timeout recovered, but
the input refusal remained and the task lease was stopped. Full keyboard,
committed Unicode, focus, mouse, rename/transform/frame interaction and multiple
native windows still require stable target-bound testing and user acceptance.
IME candidate windows, clipboard and drag-and-drop are not certified. No generic
computer-use provider was substituted.

The runtime is currently Windows with Python 3.12 or newer. Blender 4.2's older
Python can use the native sidebar, but cannot launch this Web route. Linux/macOS
transport unit tests do not certify Linux/macOS Blender Web integration.

## Shared renderer and backend checks

The renderer is the optional dependency-free `auroraview-offscreen` package. Its
portable Electron runtime is acquired separately with publisher digest and size
verification, a complete inventory and original licensing notices. Blender does
not download it. The hidden renderer owns no native focus or floating overlay.

Actual hidden-Electron probes exercised frame delivery, SDK bridge calls,
resizing, keyboard/mouse forwarding, simultaneous isolated renderer owners,
normal shutdown and parent-pipe EOF. Those checks establish renderer behavior,
not delivery through Blender's modal event handler. A real Node output benchmark
measured approximately 1.92 seconds versus 0.10 seconds for a 12 MiB frame plus
control traffic with 10 ms polling after increasing the Windows pipe buffer.
This is a transport measurement, not an end-to-end latency guarantee.

The public `BackendSession` contract and Blender's borrowed-backend integration
are source-tested for structured results/errors, subscription disposal, late
notifications, request cancellation, retries and ownership. The adapter does not
start a DCC-MCP server or copy its private dispatch/registry implementation. Direct
DCC-MCP Core 0.20.41 integration still needs public invocation and removable
subscription APIs; an existing public client or host adapter supplies the port.

## Source and packaging CI

The add-on CI tests Python 3.10 through 3.13, lint, sdist/wheel construction, source
identity in the wheel and lazy import without Blender or Core. Explicit optional
wheel packaging checks validate wheel identity, safe members, dependency and
platform declarations, metadata, licensing and exact wheel bytes in the extension.
These source checks do not launch Blender or verify visible input behavior.

Core's offscreen workflow separately runs Windows/Linux transport and wheel
contracts, Node helper contracts and explicit backend/Bridge lifecycle regressions.
The ordinary Core SDK, Python, Rust and DCC checks remain independent gates. A
required failure or running job prevents merge of that exact head.

## Historical native sidebar evidence

On 2026-10-08, Blender 4.2.3 LTS validated and installed the source-only extension.
Thirteen real-host checks covered namespace isolation, native selection/consumer
panels, idempotent registration, unregister, module reload and a save/open cycle.
The previous generation's dispatcher was rejected after file load. Core was not
imported and no Web renderer was created. DCC-CUA 1.9.1 observed native panels;
the attempted property edit left Cube unchanged and was recorded as a failure.

On 2026-10-05, a dedicated Blender 4.3.2 GUI with CPython 3.13.5 passed 21 host
registration/dispatch checks and exited normally with code 0. That probe also did
not create a Web renderer. Native `Panel.draw` controls remain distinct from the
actual HTML editor route described above.
