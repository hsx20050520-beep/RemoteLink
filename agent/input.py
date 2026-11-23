"""输入注入工厂（按平台选择实现）。"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger("remotelink.input")


def create_input():
    if sys.platform == "win32":
        from .input_win import WindowsInput
        return WindowsInput()
    if sys.platform == "darwin":
        from .input_mac import MacInput
        return MacInput()
    raise RuntimeError("目前仅支持 Windows / macOS")
