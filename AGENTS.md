# AuroraView Blender

Blender-only scheduling and UI adapter. Keep native rendering, JS bridge, RPC
wire format and generic view lifecycle in AuroraView Core.

- `src/auroraview_blender/runtime.py`: bounded main-thread queue and host session
- `src/auroraview_blender/__init__.py`: add-on register/unregister and UI
- `tests/`: host ownership tests with explicit fake transport
- Run `python -m unittest discover -s tests -v` and `ruff check .`
- Main-thread-only bpy access; workers may only enqueue callbacks
- Never turn headless/mocked tests into native GUI support claims
- No automatic package installation, external listener or eval/exec RPC
- Do not commit runtime artifacts, wheels, screenshots or test-result files
