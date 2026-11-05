"""Global push-to-talk keys (keyboard or mouse side buttons) via low-level hooks.

Key strings:  "key:ctrl_r"  "key:f13"  "key:caps_lock"  "vk:192"  "mouse:x1"  "mouse:x2"
              "mouse:middle"
"""

from __future__ import annotations

import logging
import threading

from pynput import keyboard, mouse

from .typer import INJECT_TAG

log = logging.getLogger(__name__)

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WM_MBUTTONDOWN, WM_MBUTTONUP, WM_XBUTTONDOWN, WM_XBUTTONUP = 0x0207, 0x0208, 0x020B, 0x020C

# pynput Key name -> Windows virtual key
NAMED_VK = {
    "ctrl_r": 0xA3, "ctrl_l": 0xA2, "shift_r": 0xA1, "shift_l": 0xA0, "alt_r": 0xA5,
    "alt_l": 0xA4, "caps_lock": 0x14, "scroll_lock": 0x91, "pause": 0x13, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "page_up": 0x21, "page_down": 0x22, "menu": 0x5D,
    "num_lock": 0x90, "space": 0x20, "tab": 0x09, "backspace": 0x08, "delete": 0x2E,
    "print_screen": 0x2C, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
}
for i in range(1, 25):
    NAMED_VK[f"f{i}"] = 0x6F + i
VK_NAME = {v: k for k, v in NAMED_VK.items()}
for i in range(10):
    VK_NAME[0x60 + i] = f"num{i}"


def parse(spec: str) -> tuple[str, int]:
    kind, _, val = spec.partition(":")
    if kind == "key":
        return "kb", NAMED_VK.get(val, 0)
    if kind == "vk":
        return "kb", int(val)
    if kind == "mouse":
        return "mouse", {"x1": 1, "x2": 2, "middle": 3}.get(val, 2)
    return "kb", 0


def describe(spec: str) -> str:
    kind, code = parse(spec)
    if kind == "mouse":
        return {1: "Mouse 4 (back)", 2: "Mouse 5 (forward)", 3: "Middle mouse"}[code]
    name = VK_NAME.get(code)
    if name:
        return name.replace("_r", " (right)").replace("_l", " (left)").replace("_", " ").title()
    if 0x30 <= code <= 0x5A:
        return chr(code)
    return {0xC0: "` (backtick)", 0xDC: "\\", 0xBA: ";", 0xDE: "'"}.get(code, f"key {code}")


def spec_from_vk(vk: int) -> str:
    return f"key:{VK_NAME[vk]}" if vk in VK_NAME and not VK_NAME[vk].startswith("num") \
        else f"vk:{vk}"


class HotkeyManager:
    """Calls on_down(binding_index) / on_up(binding_index). Thread-safe-ish: callbacks run on
    the hook thread, so keep them short (the app just posts to a queue)."""

    def __init__(self, on_down, on_up):
        self.on_down = on_down
        self.on_up = on_up
        self._targets: list[tuple[str, int]] = []
        self._held: set[int] = set()
        self.suppress = False
        self._kb = None
        self._ms = None
        self._capture = None        # callable(spec) while capturing a new key
        self._lock = threading.Lock()

    def set_bindings(self, specs: list[str], suppress: bool) -> None:
        with self._lock:
            self._targets = [parse(s) for s in specs]
            self._held.clear()
            self.suppress = suppress

    def capture_next(self, callback) -> None:
        """Next key/mouse button pressed is reported as a spec string (for the settings UI).
        The callback runs on the hook thread - it must only post to a queue."""
        self._capture = callback

    def cancel_capture(self) -> None:
        self._capture = None

    def start(self) -> None:
        self._kb = keyboard.Listener(win32_event_filter=self._kb_filter)
        self._ms = mouse.Listener(win32_event_filter=self._ms_filter)
        self._kb.start()
        self._ms.start()

    def stop(self) -> None:
        for l in (self._kb, self._ms):
            if l:
                l.stop()

    # ---------------------------------------------------------- filters
    def _hit(self, kind: str, code: int, down: bool) -> bool:
        matched = False
        with self._lock:
            for idx, (k, c) in enumerate(self._targets):
                if k == kind and c == code:
                    matched = True
                    if down and idx not in self._held:
                        self._held.add(idx)
                        self._safe_call(self.on_down, idx)
                    elif not down and idx in self._held:
                        self._held.discard(idx)
                        self._safe_call(self.on_up, idx)
        return matched

    # The filters run inside Windows' low-level hook. An exception here would kill the
    # listener thread (= push-to-talk silently dead), and a slow filter lags all input.
    # So: never raise (except pynput's own suppress signal), never block.
    def _kb_filter(self, msg, data):
        try:
            return self._kb_filter_inner(msg, data)
        except Exception as e:
            if type(e).__name__ == "SuppressException":
                raise
            log.exception("keyboard hook error")
            return True

    def _ms_filter(self, msg, data):
        try:
            return self._ms_filter_inner(msg, data)
        except Exception as e:
            if type(e).__name__ == "SuppressException":
                raise
            log.exception("mouse hook error")
            return True

    def _safe_call(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception:
            log.exception("hotkey callback failed")

    def _kb_filter_inner(self, msg, data):
        if data.dwExtraInfo == INJECT_TAG:
            return True                         # our own injected Enter/Ctrl+V
        down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
        if self._capture and down:
            cb, self._capture = self._capture, None
            self._safe_call(cb, None if data.vkCode == 0x1B else spec_from_vk(data.vkCode))
            self._kb.suppress_event()
        matched = self._hit("kb", data.vkCode, down)
        if matched and self.suppress:
            self._kb.suppress_event()
        return True

    def _ms_filter_inner(self, msg, data):
        if msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            code = (data.mouseData >> 16) & 0xFFFF
            down = msg == WM_XBUTTONDOWN
        elif msg in (WM_MBUTTONDOWN, WM_MBUTTONUP):
            code = 3
            down = msg == WM_MBUTTONDOWN
        else:
            return True
        if self._capture and down:
            cb, self._capture = self._capture, None
            self._safe_call(cb, {1: "mouse:x1", 2: "mouse:x2", 3: "mouse:middle"}.get(code))
            self._ms.suppress_event()
        matched = self._hit("mouse", code, down)
        if matched and self.suppress:
            self._ms.suppress_event()
        return True
