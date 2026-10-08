# AuroraView Blender

Experimental Blender host adapter for [AuroraView](https://github.com/try-auroraview/auroraview).
It provides native sidebar tools and an optional interactive HTML surface inside
an actual Blender editor area. The native Web editor is a development candidate;
user acceptance and stable release validation remain open.

Blender owns areas, GPU textures, input, registration and one main-thread timer.
The optional auroraview-offscreen package in Core owns process transport and the
hidden Chromium renderer. It reuses AuroraView's existing SDK bridge. There is no
second backend server, event loop or implicit dependency installation.

## Native Web editor

The current route requires Windows and Blender's Python 3.12+, tested during
development with Blender 5.1.1 / Python 3.13.9. The ordinary native sidebar still
works without a renderer on Blender 4.2+.

Build the optional pure-Python client wheel and verified portable renderer from
Core's packages/auroraview-offscreen. Package the wheel explicitly:

```powershell
python tools/build_extension.py --offscreen-wheel <auroraview_offscreen-wheel>
python tools/verify_extension.py --offscreen-wheel <auroraview_offscreen-wheel>
```

Install the ZIP through Blender's Extensions UI, choose the extracted renderer
bundle in add-on preferences, then use **Open Web Editor** in View3D > Sidebar >
AuroraView. It splits the active area and mounts the scene example there.
**Close Web Editors** releases the owned surfaces and renderer.

For a dedicated source demo with isolated Blender configuration:

```powershell
.\tools\launch_demo.ps1 -RendererBundle <extracted-bundle> -Client <client-wheel-or-python-directory>
```

See [native Web editor setup and public backend integration](docs/native-web.md),
[ownership decision](docs/adr/0001-native-web-surfaces.md), and
[validation boundaries](docs/validation.md).

## Existing backend integration

`open_editor(html=..., backend=session, events=(...))` borrows an existing public
BackendSession. Calls and events use its public contract; view cleanup releases
its own subscriptions and requests without closing the borrowed backend. The
default scene explorer is a small local demo, not a replacement DCC-MCP registry.

Core's BackendSession.borrow(invoke_tool=..., list_tools=..., subscribe=...)
creates no server or dispatcher. DCC-MCP Core 0.20.41 currently lacks the direct
public tool invocation and removable subscription APIs needed for in-process
attachment. Supply a public host adapter or existing MCP client; private server
fields are unsupported.

## Native sidebar and floating windows

Third-party tools can use register_panel / unregister_panel for ordinary
Blender-native controls without installing Core. See [panel API](docs/native-panels.md).
The earlier optional floating WebView route remains experimental and uses the
[pinned Core contract](docs/core-compatibility.md); it is separate from the new
native editor surface. See [consumer tools](docs/consumer-tools.md).

## Source checks

```powershell
python -m unittest discover -s tests -v
python -m ruff check src tests tools
python -m ruff format --check src tests tools
python -m build
python tools/verify_wheel.py
python tools/build_extension.py
python tools/verify_extension.py
```

CI checks Python 3.10-3.13 and source/package equality. It does not certify live
Blender rendering. Native input, lifecycle, performance and supported hosts need
separate application evidence. Version 0.1.0.dev0 is not a stable release.

MIT; see [LICENSE](LICENSE).
