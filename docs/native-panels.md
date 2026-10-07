# Native Blender tools

The adapter registers real `bpy.types.Panel` classes in View3D > Sidebar >
AuroraView. The Selection panel reads current selection and exposes Blender's
native active-object name and transform controls. These tools use Blender's
own layout, undo and property handling. They do not load AuroraView Core,
create a WebView or start a worker thread.

## Install as an extension

Build the extension with `python tools/build_extension.py`, then install the
resulting ZIP using Preferences > Get Extensions > Install from Disk in
Blender 4.2 or newer. Enable AuroraView Blender, open the View3D sidebar and
select AuroraView. Source development remains available through `src/`.
The ZIP contains the adapter and manifest, with no Core wheel or native engine.
It is a development package, not a published Blender Extensions listing.

## Add a tool

Consumers can register a native panel on the main thread after enabling the
adapter. Pass the enabled adapter module explicitly to your integration code;
Blender extensions use a repository-specific `bl_ext` module namespace. Do not
create a top-level import alias for the extension.

```python
def register_tools(adapter):
    def draw(layout, context):
        layout.label(text=f"Selected: {len(context.selected_objects)}")
        if context.active_object is not None:
            layout.prop(context.active_object, "location")

    adapter.register_panel("EXAMPLE_PT_transform", "Transform", draw)


def unregister_tools(adapter):
    adapter.unregister_panel("EXAMPLE_PT_transform")
```

The callback receives `(layout, context)` on Blender's main thread. Use the
provided context rather than caching RNA objects across file loads. Keep draw
callbacks short and free of mutations, blocking I/O and lifecycle changes.
Use native operators for actions and native properties for editing. IDs follow
Blender's `PREFIX_PT_name` convention; collisions are rejected. Consumers remove
their panels when disabled; disabling the adapter also removes every owned
panel. Failed removals retain ownership for a later cleanup retry.

## Rendering boundary

Native tool panels can dock through Blender's sidebar. They render Blender
controls. HTML/CSS/JavaScript pages still use the separate experimental Windows
floating WebView route. A WebView cannot be placed inside `Panel.draw` using
the supported Python layout API. HTML inside a Blender region would require a
Core offscreen frame/input contract and a Blender GPU integration, which are
not implemented here. Reparenting an OS window is not used.

The generic Core bridge and native renderer stay in Core; Blender registration,
panels, scheduler and lifecycle hooks stay in this repository. The older Core
Blender dispatcher remains a compatibility path, not the native panel API.

Blender documents persistent Python threads as unsupported. Scheduling a callback
onto its main thread alone does not certify a long-lived WebView callback thread.
The native panel route avoids that dependency. See the official
[threading guidance](https://docs.blender.org/api/main/info_gotchas_threading.html)
and [Panel API](https://docs.blender.org/api/5.0/bpy.types.Panel.html).
