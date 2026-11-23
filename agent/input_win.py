"""Windows 输入注入：SendInput 实现鼠标 / 键盘 / 滚轮。"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

log = logging.getLogger("remotelink.input")

user32 = ctypes.WinDLL("user32", use_last_error=True)

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


# JS KeyboardEvent.code -> Windows 虚拟键码
VK_MAP = {
    "Escape": 0x1B, "Tab": 0x09, "CapsLock": 0x14, "Space": 0x20,
    "Enter": 0x0D, "Backspace": 0x08, "Delete": 0x2E, "Insert": 0x2D,
    "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22,
    "ArrowLeft": 0x25, "ArrowUp": 0x26, "ArrowRight": 0x27, "ArrowDown": 0x28,
    "ShiftLeft": 0x10, "ShiftRight": 0x10, "ControlLeft": 0x11, "ControlRight": 0x11,
    "AltLeft": 0x12, "AltRight": 0x12, "MetaLeft": 0x5B, "MetaRight": 0x5B,
    "Minus": 0xBD, "Equal": 0xBB, "BracketLeft": 0xDB, "BracketRight": 0xDD,
    "Backslash": 0xDC, "Semicolon": 0xBA, "Quote": 0xDE, "Comma": 0xBC,
    "Period": 0xBE, "Slash": 0xBF, "Backquote": 0xC0,
    "NumpadMultiply": 0x6A, "NumpadAdd": 0x6B, "NumpadSubtract": 0x6D,
    "NumpadDecimal": 0x6E, "NumpadDivide": 0x6F, "NumpadEnter": 0x0D,
    "NumLock": 0x90, "ScrollLock": 0x91, "PrintScreen": 0x2C, "Pause": 0x13,
    "ContextMenu": 0x5D,
}
for _c in range(ord("A"), ord("Z") + 1):
    VK_MAP[f"Key{chr(_c)}"] = _c
for _d in range(10):
    VK_MAP[f"Digit{_d}"] = 0x30 + _d
    VK_MAP[f"Numpad{_d}"] = 0x60 + _d
for _f in range(1, 13):
    VK_MAP[f"F{_f}"] = 0x6F + _f

EXTENDED = {
    "ArrowLeft", "ArrowUp", "ArrowRight", "ArrowDown", "Insert", "Delete",
    "Home", "End", "PageUp", "PageDown", "ControlRight", "AltRight",
    "NumpadEnter", "NumpadDivide", "NumLock", "PrintScreen", "ContextMenu",
    "MetaLeft", "MetaRight",
}


class WindowsInput:
    def __init__(self) -> None:
        self._rect = (0, 0, 1920, 1080)

    def set_rect(self, left: int, top: int, width: int, height: int) -> None:
        self._rect = (left, top, max(width, 1), max(height, 1))

    # ---- 基础 -----------------------------------------------------------
    @staticmethod
    def _send(*inputs: INPUT) -> None:
        arr = (INPUT * len(inputs))(*inputs)
        user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))

    @staticmethod
    def _mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
        mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
        return INPUT(type=INPUT_MOUSE, mi=mi)

    @staticmethod
    def _key_input(vk: int, flags: int, scan: int = 0) -> INPUT:
        ki = KEYBDINPUT(vk & 0xFFFF, scan & 0xFFFF, flags, 0, 0)
        return INPUT(type=INPUT_KEYBOARD, ki=ki)

    # ---- 对外接口 -------------------------------------------------------
    def move(self, nx: float, ny: float) -> None:
        left, top, width, height = self._rect
        vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = max(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), 2)
        vh = max(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN), 2)
        px = left + int(nx * (width - 1))
        py = top + int(ny * (height - 1))
        ax = int((px - vx) * 65535 / (vw - 1))
        ay = int((py - vy) * 65535 / (vh - 1))
        self._send(self._mouse_input(
            MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay))

    def button(self, btn: int, down: bool) -> None:
        flags = {
            0: MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP,
            1: MOUSEEVENTF_MIDDLEDOWN if down else MOUSEEVENTF_MIDDLEUP,
            2: MOUSEEVENTF_RIGHTDOWN if down else MOUSEEVENTF_RIGHTUP,
        }.get(btn, MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP)
        self._send(self._mouse_input(flags))

    def wheel(self, dy: float, dx: float = 0.0) -> None:
        if abs(dy) >= 1e-3:
            self._send(self._mouse_input(MOUSEEVENTF_WHEEL, data=int(-dy * 120)))
        if abs(dx) >= 1e-3:
            self._send(self._mouse_input(MOUSEEVENTF_HWHEEL, data=int(dx * 120)))

    def key(self, code: str, down: bool) -> None:
        vk = VK_MAP.get(code)
        if vk is None:
            log.debug("未映射按键: %s", code)
            return
        flags = 0 if down else KEYEVENTF_KEYUP
        if code in EXTENDED:
            flags |= KEYEVENTF_EXTENDEDKEY
        self._send(self._key_input(vk, flags))

    def text(self, s: str) -> None:
        """按 Unicode 直接输入字符（支持中文），每个 UTF-16 单元一次事件。"""
        buf = s.encode("utf-16-le")
        for i in range(0, len(buf), 2):
            unit = int.from_bytes(buf[i:i + 2], "little")
            self._send(self._key_input(0, KEYEVENTF_UNICODE, unit))
            self._send(self._key_input(0, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, unit))


def create_input():
    return WindowsInput() if sys.platform == "win32" else None
