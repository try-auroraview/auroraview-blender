"""Translate Blender events without touching bpy or running browser code."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_BUTTONS = {"LEFTMOUSE": ("left", 1), "RIGHTMOUSE": ("right", 2), "MIDDLEMOUSE": ("middle", 4)}
_KEYS = {
    "RET": ("Enter", "Enter"),
    "NUMPAD_ENTER": ("Enter", "NumpadEnter"),
    "ESC": ("Escape", "Escape"),
    "SPACE": (" ", "Space"),
    "TAB": ("Tab", "Tab"),
    "BACK_SPACE": ("Backspace", "Backspace"),
    "DEL": ("Delete", "Delete"),
    "INSERT": ("Insert", "Insert"),
    "LEFT_ARROW": ("ArrowLeft", "ArrowLeft"),
    "RIGHT_ARROW": ("ArrowRight", "ArrowRight"),
    "UP_ARROW": ("ArrowUp", "ArrowUp"),
    "DOWN_ARROW": ("ArrowDown", "ArrowDown"),
    "HOME": ("Home", "Home"),
    "END": ("End", "End"),
    "PAGE_UP": ("PageUp", "PageUp"),
    "PAGE_DOWN": ("PageDown", "PageDown"),
    "LEFT_SHIFT": ("Shift", "ShiftLeft"),
    "RIGHT_SHIFT": ("Shift", "ShiftRight"),
    "LEFT_CTRL": ("Control", "ControlLeft"),
    "RIGHT_CTRL": ("Control", "ControlRight"),
    "LEFT_ALT": ("Alt", "AltLeft"),
    "RIGHT_ALT": ("Alt", "AltRight"),
    "OSKEY": ("Meta", "MetaLeft"),
    "SEMI_COLON": (";", "Semicolon"),
    "PERIOD": (".", "Period"),
    "COMMA": (",", "Comma"),
    "QUOTE": ("'", "Quote"),
    "ACCENT_GRAVE": ("`", "Backquote"),
    "MINUS": ("-", "Minus"),
    "EQUAL": ("=", "Equal"),
    "SLASH": ("/", "Slash"),
    "BACK_SLASH": ("\\", "Backslash"),
    "LEFT_BRACKET": ("[", "BracketLeft"),
    "RIGHT_BRACKET": ("]", "BracketRight"),
    "NUMPAD_PERIOD": (".", "NumpadDecimal"),
    "NUMPAD_SLASH": ("/", "NumpadDivide"),
    "NUMPAD_ASTERIX": ("*", "NumpadMultiply"),
    "NUMPAD_MINUS": ("-", "NumpadSubtract"),
    "NUMPAD_PLUS": ("+", "NumpadAdd"),
}
_DIGITS = dict(
    zip(
        ("ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE"),
        "0123456789",
        strict=True,
    )
)


def modifiers(event: Any) -> int:
    """CDP modifier mask, independent of platform-specific virtual key codes."""
    return sum(
        bit
        for name, bit in (("alt", 1), ("ctrl", 2), ("oskey", 4), ("shift", 8))
        if getattr(event, name, False)
    )


def key_identity(event: Any) -> tuple[str, str] | None:
    kind = event.type
    if kind in _KEYS:
        return _KEYS[kind]
    if len(kind) == 1 and "A" <= kind <= "Z":
        return (kind if getattr(event, "shift", False) else kind.lower(), "Key" + kind)
    if kind in _DIGITS:
        digit = _DIGITS[kind]
        return digit, "Digit" + digit
    if kind.startswith("NUMPAD_") and len(kind) == 8 and kind[-1].isdigit():
        return kind[-1], "Numpad" + kind[-1]
    if kind.startswith("F") and kind[1:].isdigit() and 1 <= int(kind[1:]) <= 24:
        return kind, kind
    return None


@dataclass
class InputState:
    focused: bool = False
    buttons: dict[str, int] = field(default_factory=dict)
    clicks: dict[str, int] = field(default_factory=dict)
    keys: dict[str, str] = field(default_factory=dict)
    x: float = 0
    y: float = 0

    def release(self) -> list[dict[str, Any]]:
        had_focus = self.focused
        events = [
            {"type": "keyUp", "key": key, "code": code, "modifiers": 0}
            for code, key in self.keys.items()
        ]
        self.keys.clear()
        for button in tuple(self.buttons):
            self.buttons.pop(button)
            events.append(
                {
                    "type": "mouseUp",
                    "button": button,
                    "buttons": sum(self.buttons.values()),
                    "click_count": self.clicks.pop(button, 1),
                    "x": self.x,
                    "y": self.y,
                    "modifiers": 0,
                }
            )
        self.focused = False
        if had_focus:
            events.append({"type": "focus", "focused": False})
        return events


def translate(
    event: Any, state: InputState, *, inside: bool, x: float, y: float
) -> tuple[list[dict[str, Any]], bool]:
    """Return protocol events and whether the owned HTML surface consumed input."""
    kind, value = event.type, getattr(event, "value", "NOTHING")
    mask = modifiers(event)
    if kind == "WINDOW_DEACTIVATE":
        return state.release(), False
    if kind in _BUTTONS:
        button, bit = _BUTTONS[kind]
        if value in {"PRESS", "DOUBLE_CLICK"}:
            if not inside:
                return state.release(), False
            events = [] if state.focused else [{"type": "focus", "focused": True}]
            state.focused = True
            state.buttons[button] = bit
            state.clicks[button] = 2 if value == "DOUBLE_CLICK" else 1
            state.x, state.y = x, y
            events.append(
                {
                    "type": "mouseDown",
                    "button": button,
                    "buttons": sum(state.buttons.values()),
                    "click_count": state.clicks[button],
                    "x": x,
                    "y": y,
                    "modifiers": mask,
                }
            )
            return events, True
        if value == "RELEASE" and button in state.buttons:
            state.buttons.pop(button)
            state.x, state.y = x, y
            return [
                {
                    "type": "mouseUp",
                    "button": button,
                    "buttons": sum(state.buttons.values()),
                    "click_count": state.clicks.pop(button, 1),
                    "x": x,
                    "y": y,
                    "modifiers": mask,
                }
            ], True
        return [], False
    if kind in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
        if not inside and not state.buttons:
            return [], False
        state.x, state.y = x, y
        return [
            {
                "type": "mouseMove",
                "buttons": sum(state.buttons.values()),
                "x": x,
                "y": y,
                "modifiers": mask,
            }
        ], bool(state.buttons)
    if kind in {"WHEELUPMOUSE", "WHEELDOWNMOUSE", "WHEELINMOUSE", "WHEELOUTMOUSE"} and inside:
        delta = -100 if kind in {"WHEELUPMOUSE", "WHEELINMOUSE"} else 100
        return [
            {
                "type": "mouseWheel",
                "delta_x": 0,
                "delta_y": delta,
                "x": x,
                "y": y,
                "modifiers": mask,
            }
        ], True
    if not state.focused:
        return [], False
    text = getattr(event, "unicode", "")
    if kind == "TEXTINPUT" and text and value != "RELEASE":
        return [{"type": "text", "text": text}], True
    identity = key_identity(event)
    events = []
    if identity and value in {"PRESS", "RELEASE"}:
        key, code = identity
        if value == "PRESS":
            if text and text.isprintable() and not mask & (1 | 2 | 4):
                key = text
            state.keys[code] = key
        else:
            key = state.keys.pop(code, key)
        events.append(
            {
                "type": "keyDown" if value == "PRESS" else "keyUp",
                "key": key,
                "code": code,
                "modifiers": mask,
            }
        )
    # CDP raw key events do not insert characters; insert the committed Unicode separately.
    if text and value == "PRESS" and not mask & (1 | 2 | 4) and text.isprintable():
        events.append({"type": "text", "text": text})
    return events, bool(events)
