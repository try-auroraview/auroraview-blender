# ADR 0001: Native areas with an optional process-isolated renderer

Status: accepted for an experimental development route.

## Context

Blender's Python panel API provides native controls, but does not embed HTML.
Its application and GPU APIs require the main thread. A native child WebView
overlay cannot reliably follow multiple editor areas, windows and file loads.
Hosts must reuse shared DCC-MCP backends without stopping borrowed services.

## Decision

Use Blender's `IMAGE_EDITOR` WINDOW draw handler and modal operator. Core's
optional offscreen package provides bounded frames/input/RPC over owned process
pipes and reuses the existing AuroraView SDK bridge. Blender owns only its native
area, texture, pointer identity and main-thread lifecycle. Its existing scheduler
pumps this transport; no second event loop or rendering thread touches `bpy`.

Backend calls use an explicit borrowed public session. The demo scene adapter
is limited to the example. Neither route introduces a DCC-MCP service, registry
or private import. Explicit ownership determines which resources may be stopped.

## Consequences

This keeps host code small and renderer fixes shared with other hosts, and gives
native docking/resizing rather than an OS overlay. Software frames consume CPU
and transfer bandwidth; dimensions, queues and pixel budgets are bounded. The
initial route requires Windows Python 3.12+ and an explicitly installed verified
runtime. GPU zero-copy, other platforms, complete IME and richer browser-native
features require separate work and acceptance. Source checks, hidden renderer
checks, actual Blender UI evidence and user acceptance remain separate gates.
