# One-window input acceptance

This is a pending acceptance procedure, not evidence that input works in Blender.
The modal tests use fake Blender and renderer objects. They cover exact-window
routing, shortcut/Unicode forwarding, focus release and delivery-error readback;
they cannot reproduce OS foreground activation or certify HTML text insertion.

## Before launching

Wait for the allocated desktop slot. Freeze the Blender commit, extension/client
wheel and renderer inventory with sizes and SHA-256 digests. Record the actual
Blender/Python versions after launch; executable file metadata is insufficient.
Use one new factory-startup Blender window and an isolated configuration/temp
directory. Record the original process handle, PID and start time so its exit
code can be retained. Do not take over an existing Blender session.

`tools/launch_demo.ps1` accepts `-Blender`, `-RendererBundle`, `-Client` and a fresh
`-Evidence` path. It launches development source, not an installed extension.
Its launcher exit code is not the Blender exit code. Installed-extension checks
belong to `tests/blender_offscreen_probe.py`, in a separate run; that probe does
not send UI input. Neither entry point downloads dependencies.

Before the first UI observation or input, report and attest:

```text
provider=dcc-cua runtime=<version> pid=<new Blender PID> hwnd=<exact window>
```

Use the project DCC-CUA/ui-control route throughout. Refused activation, a busy
channel or missing identity binding stops dependent input. Preserve that result;
do not switch providers or create another host to bypass it.

## Input and readback

1. Read the exact target's foreground relationship and scene state. Click the
   Web demo's Cube selection/name field. Verify the HTML effect and scene revision
   through structured readback, alongside the surface ID and generation.
2. Send Ctrl+A, then the nonsecret committed string `Aurora测试Cube`. Read Blender's
   input counter and bounded `last_input` summary, renderer error state, and HTML
   field value. Raw Unicode/text must not be added to event diagnostics. A consumed
   modal event or DCC-CUA acknowledgement alone does not establish delivery.
3. Apply the rename and verify the object's exact name and scene revision. Exercise
   the demo's position and frame controls and read their resulting scene values.
   Recheck the target relationship before each input sequence.
4. Leave the field/window and verify focus/key/button release. After focus loss,
   unfocused keys must pass through. Native borders, header and sidebar retain
   their normal Blender behavior.

Record each boundary separately: OS target, Blender event receipt, translation/
renderer delivery, HTML effect, scene mutation. If the chain stops, report the
first unproven boundary and preserve original errors. Do not infer a root cause
from a screenshot or from source tests. Main-thread Blender/GPU ownership remains
required; renderer workers never access `bpy` or own a floating overlay.

## Resources and cleanup

Retain an ownership list containing the Blender process/window, renderer process
tree, native surface IDs/generations, modal handler, draw handler, timer, scene
subscriptions, isolated directories and evidence/log paths. Close owned editors,
cancel subscriptions and request normal demo stop with the evidence path's
`.stop` sibling (replace the evidence file suffix with `.stop`). Read back empty
owners, renderer closure and the retained Blender
exit code, stdout and stderr. Process disappearance alone is insufficient.

On failed cleanup, preserve ownership and evidence for diagnosis; do not delete
directories or kill unrelated processes. Report normal exit as failed or unknown
unless its original exit code and cleanup readback establish success.

The acceptance scope is one Windows Blender Web window with Python 3.12 or newer.
Multiple windows, IME candidate UI, clipboard and drag-and-drop remain separate
gates. The Core-free native sidebar has its own compatibility and acceptance
scope. No merge or release follows automatically from this procedure.
