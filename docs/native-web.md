# Native Web editor

The add-on mounts an offscreen Web page in an actual `IMAGE_EDITOR` WINDOW region.
The hidden renderer never owns native focus. Blender supplies the drawing area,
modal input and GPU upload; there is no floating overlay or screenshot loop.

## Install and run

1. Build `auroraview-offscreen`'s dependency-free wheel and portable runtime in
   AuroraView Core. Its maintainer tools verify the publisher's Electron digest
   and package a manifest covering runtime files and licensing notices.
2. Build this extension with `--offscreen-wheel <wheel>` and verify with the same
   argument. Blender's extension system installs that bundled wheel.
3. Extract the portable runtime to a user-selected directory. Set **Renderer
   bundle** in AuroraView Blender preferences. No runtime download happens in
   Blender. Windows with Python 3.12+ is required for nonblocking anonymous pipes.
4. In a 3D View's AuroraView sidebar, press **Open Web Editor**. Use the HTML scene
   list, name/position controls and scene-frame field. Native editor borders and
   headers remain Blender-owned. Close with **Close Web Editors**.

For development, `tools/launch_demo.ps1` accepts `-Blender`, `-RendererBundle`,
`-Client` (wheel or its Python source directory), and optional `-Evidence <json>`.
It launches a dedicated factory-startup GUI with a temporary configuration.
Evidence records actual host/renderer identity, region dimensions, sequences,
scene readback and errors. Creating its `.stop` sibling requests owned cleanup
and normal Blender exit. Stdout/stderr are saved alongside the evidence JSON.
`-WaitForOpen` waits for the ordinary launch operator instead of opening on start.

The launcher is a development entry, not an installer. Native Web editors on
Blender 4.2's older Python are explicitly unavailable; its sidebar controls still
work. Linux/macOS native Web editor integration is not implemented.

## Public backend port

```python
from auroraview.integration.backend import BackendSession
import auroraview_blender

# Public host adapter; Future scheduling belongs to its existing event loop.
session = BackendSession.borrow(
    invoke_tool=host.call_tool,
    list_tools=host.list_tools,
    subscribe=host.subscribe,  # returns a synchronous dispose callable
)
auroraview_blender.open_editor(
    html=my_html,
    backend=session,
    events=("scene.changed",),
)
# The page uses auroraview.call / auroraview.on. Closing the view does not stop
# this session or the shared host runtime. Its caller later closes the session.
```

One native-editor group borrows one backend. Close that group before changing
backends. At most 32 named subscriptions and 32 pending requests are retained.
Requests are cancelled when their surface generation disappears; responses from
old generations are discarded. A bare coroutine is rejected and closed instead
of starting a second event loop. MCP result/error payloads pass through unchanged;
the page is responsible for interpreting its backend's schema.

The bundled HTML uses six bounded local scene example methods. It does not
discover, register or start a DCC-MCP server. Production integrations supply their
existing public backend, rather than copying this demo registry. DCC-MCP Core
0.20.41 needs a public invocation/removable-subscription extension for direct
in-process use. No private DCC-MCP APIs are imported here.

## Ownership and input

All `bpy`, GPU and input processing runs on Blender's main thread through the
existing timer. A surface binds window/area/region/space pointer and generation;
changing editor type or closing an area invalidates it. Eight surfaces share one
owned renderer process. Logical dimensions follow Blender's UI scale, bounded
by 4096 pixels per dimension and four million pixels per surface.

Mouse coordinates map from Blender's bottom-left origin to the renderer's
top-left origin. Native headers, overlapping regions and resize edges pass
through. Button capture, modifiers, physical keys and committed Unicode are
forwarded to Chromium's input API. Leaving native focus releases pressed keys
and buttons. Web rendering is CPU based; texture transfer and host timer cadence
bound actual responsiveness. IME composition/candidate-window integration,
clipboard and drag-and-drop are not certified.

Closing releases textures, handlers, subscriptions and requests. Normal renderer
shutdown is pumped asynchronously; explicit unregister/exit reaps only the owned
process tree with one shared deadline of at most three seconds; ordinary timer
cleanup keeps its 250 ms budget. Cleanup failure retains ownership for retry.
Hosts exposing the public persistent `exit_pre` handler clean up before native
data is dismantled. Older hosts retain an `atexit` fallback. The executing handler
list is left intact so another add-on's following exit callback is not skipped.
No unrelated Blender or renderer process is terminated. See [validation](validation.md)
for the exact tested host and remaining interactive acceptance gates.
