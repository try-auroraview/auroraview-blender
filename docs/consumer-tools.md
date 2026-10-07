# Register a third-party tool before show

`BlenderSession.open(..., configure=configure)` is the public pre-show hook for
consumer commands and events. It receives the public Core WebView, on Blender's
main thread, after the adapter installs its call dispatcher and `blender.context`
binding, and before `show(wait=False)` transfers native ownership. Register new
methods and events here rather than after `open` returns.

## Prerequisites and limits

Install the adapter and an explicitly reviewed, compatible Core build into the
Python environment used by Blender, then install your own add-on separately.
The [Core contract](core-compatibility.md) still has no declared compatible
released wheel; this is a development-source example, not a ready-to-install
native-certified release. Do not substitute the released Core 0.5.11 wheel.

Only the existing experimental Windows floating-window route is available.
Linux/macOS remain rejected. Native panel embedding is not implemented, and
this hook does not change capabilities or enable the separate GTK experiment.
There is no renderer, Core replacement, or product demo dependency in this
consumer recipe.

## Minimal independent add-on

The example owns one session and view, uses only exported adapter/Core APIs,
and reads real Blender selection when its command runs. Keep the following in
your own add-on module; use your organization's reverse-DNS identifiers.

```python
import bpy
from auroraview_blender import BlenderSession

VIEW_ID = "org.example.selection_tool"
METHOD = "org.example.selection_tool.read"
EVENT = "org.example.selection_tool.read_completed"
session = None

HTML = """<!doctype html><html><meta charset="utf-8">
<title>Selection tool</title><button id="read">Read selection</button>
<pre id="result">Select an object in Blender, then click Read selection.</pre>
<script>
document.getElementById('read').onclick = async () => {
  try {
    const names = await window.auroraview.call('org.example.selection_tool.read');
    document.getElementById('result').textContent = JSON.stringify(names);
  } catch (error) {
    document.getElementById('result').textContent = String(error);
  }
};
</script></html>"""


def configure(view):
    def read_selection():
        # bind_call uses the adapter's Blender main-thread dispatcher.
        names = [obj.name for obj in bpy.context.selected_objects]
        view.emit(EVENT, {"names": names})
        return names

    view.bind_call(METHOD, read_selection, allow_rebind=False)


def register():
    global session
    if session is not None:
        return  # This add-on already owns a session.
    session = BlenderSession(bpy)
    try:
        session.start()
        session.open(VIEW_ID, title="Selection tool", html=HTML, configure=configure)
    except BaseException:
        unregister()
        raise


def unregister():
    global session
    if session is not None:
        session.stop()  # Requests Core close, stops timer, drops queued work.
        session = None  # Retain ownership for retry if stop raises.
```

For a JavaScript-to-Python event, register `view.on(event_name, handler)` inside
`configure`, retaining its returned connection ID if needed. Selective removal
uses `view.disconnect(event_name, connection_id)`. The adapter's dispatcher
covers `bind_call`/`bind_api`; event delivery uses Core's separate DCC dispatcher.
Do not assume this hook changes event-thread guarantees or touch `bpy` from an
unverified event callback. Use `bind_call` for host commands as shown above.

## Ownership and repeated calls

- `configure` must be `None` or a synchronous callable accepting the view. A
  non-callable is rejected before construction, including on live-view reuse
- The hook runs once per newly constructed view. It should only register APIs;
  do not show/close the view or change session lifecycle from inside it
- Opening an already-live ID returns the same view without rerunning the hook
  or applying new options. This is reuse, not reconfiguration
- `session.close(VIEW_ID)` requests close. A later `open` creates a fresh view;
  pass `configure=configure` again. The session does not remember that callback
- If configuration or show fails, the adapter requests close and re-raises.
  Other owned views and the already-started session stay available. If close
  itself fails, the new view remains in `session.views` so `close`/`stop` can
  retry; the cleanup error is raised with the original error as its context
- Closing is a request, not proof of native termination. A retained Core view's
  `wait(0)` can check completion without blocking Blender. Do not block the host
  main thread waiting for work that needs it
- One consumer should own each session/view. Namespaces avoid accidental naming
  collisions; they are not leases or a security boundary. `allow_rebind=False`
  skips existing bindings; it is not collision detection
- Call `stop()` on add-on unregister and before replacing the session on file
  load/module reload. Independent consumers own those hooks; the adapter's
  built-in launcher does not manage third-party sessions

## Required native acceptance

Source tests use explicit host/Core doubles and bounded subprocesses. They prove
configuration ordering and scheduler behavior, not rendering. Before a native
support claim, install the exact adapter/Core builds in real Windows Blender,
register the independent add-on, read actual selection through the page,
close/reopen, repeat with a second namespaced consumer, then unregister and
verify view termination and timer/handler cleanup. No native pass is claimed
for this example.
