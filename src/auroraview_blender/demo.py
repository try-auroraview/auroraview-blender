"""Bundled scene demo; applications may provide their own HTML or URL."""

from pathlib import Path

HTML = (Path(__file__).parent / "assets" / "scene.html").read_text(encoding="utf-8")
