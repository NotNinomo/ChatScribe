"""Getting text into the WoW chat box: focus check, clipboard paste, SendInput keys."""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --------------------------------------------------------------- SendInput types
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
VK_RETURN, VK_CONTROL, VK_SHIFT, VK_MENU, VK_ESCAPE = 0x0D, 0x11, 0x10, 0x12, 0x1B
VK_RCONTROL, VK_LCONTROL, VK_V, VK_A, VK_BACK = 0xA3, 0xA2, 0x56, 0x41, 0x08
MAPVK_VK_TO_VSC = 0
ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _INPUTunion(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short

# Marker so our own hotkey listener can ignore keys we inject.
INJECT_TAG = 0xC5C5


def _key(vk: int, up: bool = False) -> INPUT:
    scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    flags = KEYEVENTF_KEYUP if up else 0
    return INPUT(INPUT_KEYBOARD, _INPUTunion(ki=KEYBDINPUT(vk, scan, flags, 0, INJECT_TAG)))


def _uni(ch: int, up: bool = False) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(INPUT_KEYBOARD, _INPUTunion(ki=KEYBDINPUT(0, ch, flags, 0, INJECT_TAG)))


def _send(*inputs: INPUT) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        log.warning("SendInput blocked (%s). If WoW runs as administrator, run ChatScribe "
                    "as administrator too.", ctypes.get_last_error())


def tap(vk: int) -> None:
    _send(_key(vk), _key(vk, True))


def chord(mod: int, vk: int) -> None:
    _send(_key(mod), _key(vk), _key(vk, True), _key(mod, True))


def type_unicode(text: str, delay: float = 0.0) -> None:
    for ch in text:
        code = ord(ch)
        if code > 0xFFFF:           # outside BMP - WoW can't draw it anyway
            continue
        _send(_uni(code), _uni(code, True))
        if delay:
            time.sleep(delay)


def release_modifiers() -> None:
    """If the user is still physically holding Ctrl/Shift/Alt, our Enter would become Ctrl+Enter."""
    for vk in (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5):   # L/R shift, ctrl, alt
        if user32.GetAsyncKeyState(vk) & 0x8000:
            _send(_key(vk, True))


# ------------------------------------------------------------------- clipboard
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
user32.SetClipboardData.restype = wintypes.HANDLE
user32.OpenClipboard.argtypes = (wintypes.HWND,)


def _open_clipboard() -> bool:
    for _ in range(20):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.01)
    return False


def get_clipboard() -> str | None:
    if not _open_clipboard():
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def set_clipboard(text: str) -> bool:
    if not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        data = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(data)
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, data, size)
        kernel32.GlobalUnlock(h)
        return bool(user32.SetClipboardData(CF_UNICODETEXT, h))
    finally:
        user32.CloseClipboard()


# -------------------------------------------------------------- window checks
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)


def foreground_process() -> tuple[int, str]:
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if h:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                name = os.path.basename(buf.value)
        finally:
            kernel32.CloseHandle(h)
    return hwnd, name


def wow_is_focused(process_names: list[str]) -> bool:
    _, name = foreground_process()
    return name.lower() in {p.lower() for p in process_names}


# ------------------------------------------------------------------ sending
class ChatSender:
    def __init__(self, cfg):
        self.cfg = cfg
        self._restore_timer: threading.Timer | None = None
        self._saved_clipboard: str | None = None

    def send(self, lines: list[str]) -> str:
        """Returns a short status string for the UI."""
        if not lines:
            return "nothing to send"
        cfg = self.cfg
        focused = wow_is_focused(cfg.wow_processes)
        if cfg.only_when_wow_focused and not focused:
            set_clipboard("\n".join(lines))
            return "WoW not focused - copied to clipboard"
        d = cfg.key_delay_ms / 1000
        old = None
        if cfg.restore_clipboard:
            if self._restore_timer is not None and self._restore_timer.is_alive():
                # Previous message's restore hasn't run yet: keep the user's ORIGINAL clipboard.
                self._restore_timer.cancel()
                old = self._saved_clipboard
            else:
                old = get_clipboard()
        release_modifiers()
        time.sleep(d)
        try:
            for i, line in enumerate(lines):
                if cfg.open_chat_first:
                    tap(VK_RETURN)          # open the chat box
                    time.sleep(d * 2)
                if set_clipboard(line):
                    time.sleep(d)
                    chord(VK_CONTROL, VK_V)
                else:
                    type_unicode(line, 0.002)
                time.sleep(d * 2)
                last = i == len(lines) - 1
                if cfg.auto_send or not last:
                    tap(VK_RETURN)
                    time.sleep(d * 3)
        finally:
            if old is not None:
                # WoW reads the clipboard when it processes Ctrl+V, which can be a few frames
                # later if the game hitches. Restoring too early would paste whatever you had
                # copied before INTO CHAT - so wait generously, and only restore if the
                # clipboard still holds our text (don't clobber something you copied since).
                ours = lines[-1]

                def restore():
                    if get_clipboard() == ours:
                        set_clipboard(old)
                t = threading.Timer(0.6, restore)   # don't hold up the next message
                t.daemon = True
                self._saved_clipboard = old
                self._restore_timer = t
                t.start()
        n = len(lines)
        verb = "sent" if cfg.auto_send else "typed (press Enter to send)"
        return f"{verb}" + (f" in {n} parts" if n > 1 else "")
