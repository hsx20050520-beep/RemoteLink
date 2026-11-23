"""macOS 输入注入：Quartz CGEvent。

需要在 系统设置 -> 隐私与安全性 中为终端/Python 打开：
  - 辅助功能（发送鼠标键盘事件）
  - 屏幕录制（采集画面）
"""

from __future__ import annotations

import logging

log = logging.getLogger("remotelink.input.mac")

MAC_KEYCODES = {
    "KeyA": 0, "KeyS": 1, "KeyD": 2, "KeyF": 3, "KeyH": 4, "KeyG": 5,
    "KeyZ": 6, "KeyX": 7, "KeyC": 8, "KeyV": 9, "KeyB": 11, "KeyQ": 12,
    "KeyW": 13, "KeyE": 14, "KeyR": 15, "KeyY": 16, "KeyT": 17,
    "Digit1": 18, "Digit2": 19, "Digit3": 20, "Digit4": 21, "Digit6": 22,
    "Digit5": 23, "Equal": 24, "Digit9": 25, "Digit7": 26, "Minus": 27,
    "Digit8": 28, "Digit0": 29, "BracketRight": 30, "KeyO": 31, "KeyU": 32,
    "BracketLeft": 33, "KeyI": 34, "KeyP": 35, "Enter": 36, "KeyL": 37,
    "KeyJ": 38, "Quote": 39, "KeyK": 40, "Semicolon": 41, "Backslash": 42,
    "Comma": 43, "Slash": 44, "KeyN": 45, "KeyM": 46, "Period": 47,
    "Tab": 48, "Space": 49, "Backquote": 50, "Backspace": 51, "Escape": 53,
    "MetaRight": 54, "MetaLeft": 55, "ShiftLeft": 56, "CapsLock": 57,
    "AltLeft": 58, "ControlLeft": 59, "ShiftRight": 60, "AltRight": 61,
    "ControlRight": 62, "F17": 64, "NumpadDecimal": 65, "NumpadMultiply": 67,
    "NumpadAdd": 69, "NumLock": 71, "NumpadDivide": 75, "NumpadEnter": 76,
    "NumpadSubtract": 78, "Numpad0": 82, "Numpad1": 83, "Numpad2": 84,
    "Numpad3": 85, "Numpad4": 86, "Numpad5": 87, "Numpad6": 88, "Numpad7": 89,
    "Numpad8": 91, "Numpad9": 92, "F5": 96, "F6": 97, "F7": 98, "F3": 99,
    "F8": 100, "F9": 101, "F11": 103, "F13": 105, "F16": 106, "F14": 107,
    "F10": 109, "F12": 111, "F15": 113, "Home": 115, "PageUp": 116,
    "Delete": 117, "F4": 118, "End": 119, "F2": 120, "PageDown": 121,
    "F1": 122, "ArrowLeft": 123, "ArrowRight": 124, "ArrowDown": 125,
    "ArrowUp": 126, "Insert": 114,
}


class MacInput:
    def __init__(self) -> None:
        import Quartz  # noqa: F401  (延迟导入，仅 macOS 需要)
        self._q = Quartz
        self._rect = (0.0, 0.0, 1920.0, 1080.0)
        self._scale = (1.0, 1.0)   # 采集像素 / 逻辑点

    def set_rect(self, left: int, top: int, width: int, height: int) -> None:
        """rect 来自 mss（像素坐标）。Quartz 使用逻辑点坐标。"""
        q = self._q
        bounds = q.CGDisplayBounds(q.CGMainDisplayID())
        px_w, px_h = width, height
        pt_w = bounds.size.width or px_w
        pt_h = bounds.size.height or px_h
        self._scale = (pt_w / px_w, pt_h / px_h)
        self._rect = (left, top, px_w, px_h)

    def move(self, nx: float, ny: float) -> None:
        q = self._q
        left, top, w, h = self._rect
        sx, sy = self._scale
        x = (left + nx * (w - 1)) * sx
        y = (top + ny * (h - 1)) * sy
        ev = q.CGEventCreateMouseEvent(None, q.kCGEventMouseMoved, (x, y), 0)
        q.CGEventPost(q.kCGHIDEventTap, ev)

    def button(self, btn: int, down: bool) -> None:
        q = self._q
        b = {0: (q.kCGEventLeftMouseDown, q.kCGEventLeftMouseUp, q.kCGMouseButtonLeft),
             1: (q.kCGEventOtherMouseDown, q.kCGEventOtherMouseUp, q.kCGMouseButtonCenter),
             2: (q.kCGEventRightMouseDown, q.kCGEventRightMouseUp, q.kCGMouseButtonRight)}
        down_ev, up_ev, mb = b.get(btn, b[0])
        left, top, w, h = self._rect
        sx, sy = self._scale
        ev = q.CGEventCreateMouseEvent(None, down_ev if down else up_ev,
                                       (left * sx, top * sy), mb)
        q.CGEventPost(q.kCGHIDEventTap, ev)

    def wheel(self, dy: float, dx: float = 0.0) -> None:
        q = self._q
        ev = q.CGEventCreateScrollWheelEvent(None, q.kCGScrollEventUnitLine, 2,
                                            int(-dy), int(dx))
        q.CGEventPost(q.kCGHIDEventTap, ev)

    def key(self, code: str, down: bool) -> None:
        q = self._q
        kc = MAC_KEYCODES.get(code)
        if kc is None:
            log.debug("未映射按键: %s", code)
            return
        ev = q.CGEventCreateKeyboardEvent(None, kc, down)
        q.CGEventPost(q.kCGHIDEventTap, ev)

    def text(self, s: str) -> None:
        q = self._q
        for ch in s:
            ev = q.CGEventCreateKeyboardEvent(None, 0, True)
            q.CGEventKeyboardSetUnicodeString(ev, len(ch), ch.encode("utf-16-le"))
            q.CGEventPost(q.kCGHIDEventTap, ev)
            ev = q.CGEventCreateKeyboardEvent(None, 0, False)
            q.CGEventKeyboardSetUnicodeString(ev, len(ch), ch.encode("utf-16-le"))
            q.CGEventPost(q.kCGHIDEventTap, ev)
