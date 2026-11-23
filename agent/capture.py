"""屏幕采集（Windows / macOS）。"""

from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger("remotelink.capture")


def enable_dpi_awareness() -> None:
    """Windows: 让进程感知高 DPI，否则只能抓到缩放后的模糊画面。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def list_monitors() -> list[dict]:
    import mss
    with mss.mss() as sct:
        return [dict(m) for m in sct.monitors]


def describe_monitors() -> str:
    lines = []
    for idx, m in enumerate(list_monitors()):
        tag = "全部屏幕合并" if idx == 0 else f"显示器 {idx}"
        lines.append(f"  [{idx}] {tag}: {m['width']}x{m['height']} @ ({m['left']},{m['top']})")
    return "\n".join(lines)
