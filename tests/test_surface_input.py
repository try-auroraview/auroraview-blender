"""Protocol input tests; these do not certify Blender or Chromium GUI input."""

import unittest
from types import SimpleNamespace

from auroraview_blender.input import InputState, key_identity, modifiers, translate


def event(kind, value="PRESS", **kwargs):
    return SimpleNamespace(type=kind, value=value, **kwargs)


class SurfaceInputTests(unittest.TestCase):
    def setUp(self):
        self.state = InputState()

    def send(self, kind, value="PRESS", *, inside=True, x=20, y=30, **kwargs):
        return translate(event(kind, value, **kwargs), self.state, inside=inside, x=x, y=y)

    def focus(self):
        self.send("LEFTMOUSE")
        self.send("LEFTMOUSE", "RELEASE")

    def test_click_acquires_focus_and_double_click_count_survives_release(self):
        messages, consumed = self.send("LEFTMOUSE", "DOUBLE_CLICK")
        self.assertTrue(consumed)
        self.assertEqual(messages[0], {"type": "focus", "focused": True})
        self.assertEqual(messages[1]["click_count"], 2)
        messages, consumed = self.send("LEFTMOUSE", "RELEASE", inside=False, x=-1)
        self.assertTrue(consumed)
        self.assertEqual(messages[0]["click_count"], 2)
        self.assertEqual(messages[0]["buttons"], 0)

    def test_pointer_capture_releases_outside_and_ordinary_hover_passes_through(self):
        messages, consumed = self.send("MOUSEMOVE")
        self.assertEqual(messages[0]["type"], "mouseMove")
        self.assertFalse(consumed)
        self.send("LEFTMOUSE")
        messages, consumed = self.send("MOUSEMOVE", inside=False, x=-10)
        self.assertTrue(consumed)
        self.assertEqual(messages[0]["buttons"], 1)
        self.send("LEFTMOUSE", "RELEASE", inside=False)
        self.assertEqual(self.send("MOUSEMOVE", inside=False), ([], False))

    def test_wheel_and_modifiers(self):
        messages, consumed = self.send("WHEELUPMOUSE", alt=True, shift=True)
        self.assertTrue(consumed)
        self.assertEqual(messages[0]["delta_y"], -100)
        self.assertEqual(messages[0]["modifiers"], 9)
        self.assertEqual(self.send("WHEELDOWNMOUSE", inside=False), ([], False))
        self.assertEqual(modifiers(event("A", alt=True, ctrl=True, oskey=True, shift=True)), 15)

    def test_committed_unicode_does_not_duplicate_raw_key_text(self):
        self.focus()
        messages, consumed = self.send("A", unicode="你")
        self.assertTrue(consumed)
        self.assertEqual([message["type"] for message in messages], ["keyDown", "text"])
        self.assertEqual(messages[0]["code"], "KeyA")
        self.assertEqual(messages[0]["key"], "你")
        self.assertEqual(messages[1], {"type": "text", "text": "你"})
        messages, _ = self.send("TEXTINPUT", unicode="输入")
        self.assertEqual(messages, [{"type": "text", "text": "输入"}])

    def test_shortcuts_navigation_and_unfocused_keys(self):
        self.assertEqual(self.send("A", unicode="a"), ([], False))
        self.focus()
        messages, consumed = self.send("A", unicode="a", ctrl=True)
        self.assertTrue(consumed)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["modifiers"], 2)
        for kind, key in (("BACK_SPACE", "Backspace"), ("TAB", "Tab"), ("RET", "Enter")):
            messages, consumed = self.send(kind)
            self.assertTrue(consumed)
            self.assertEqual(messages[0]["key"], key)
        self.assertEqual(key_identity(event("F12")), ("F12", "F12"))
        self.assertEqual(key_identity(event("THREE")), ("3", "Digit3"))
        self.assertEqual(key_identity(event("NUMPAD_3")), ("3", "Numpad3"))
        self.assertEqual(key_identity(event("NUMPAD_PLUS")), ("+", "NumpadAdd"))
        self.assertEqual(self.send("TIMER"), ([], False))

    def test_release_preserves_down_key_identity_if_modifiers_change(self):
        self.focus()
        self.send("A", shift=True, unicode="A")
        messages, _ = self.send("A", "RELEASE", shift=False)
        self.assertEqual(messages[0]["key"], "A")

    def test_outside_click_and_window_deactivate_release_all_input(self):
        for kind in ("LEFTMOUSE", "WINDOW_DEACTIVATE"):
            self.send("LEFTMOUSE")
            self.send("A", ctrl=True)
            messages, consumed = self.send(kind, inside=False)
            self.assertFalse(consumed)
            self.assertEqual(
                [message["type"] for message in messages], ["keyUp", "mouseUp", "focus"]
            )
            self.assertFalse(self.state.focused)
            self.assertFalse(self.state.buttons)
            self.assertFalse(self.state.keys)


if __name__ == "__main__":
    unittest.main()
