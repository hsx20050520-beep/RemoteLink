"""推流会话：采集线程 + 编码器生命周期 + 断线/改配置时的重建。"""

from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable

from .capture import enable_dpi_awareness
from .encoder import H264Encoder, resolve_encoder

log = logging.getLogger("remotelink.session")


@dataclass(frozen=True)
class StreamConfig:
    monitor: int = 1
    fps: int = 30
    quality: str = "original"
    encoder: str = "auto"      # auto / nvenc / qsv / amf / videotoolbox / x264
    bitrate: int = 0           # 码率上限 kbps，0 = 用画质预设的天花板

    def with_overrides(self, **kwargs) -> "StreamConfig":
        clean = {k: v for k, v in kwargs.items() if v is not None}
        return replace(self, **clean)


class ScreenStream:
    """在独立线程里「采集 -> 编码」，编码结果通过回调交给事件循环。"""

    def __init__(self, push_au: Callable[[bytes, bool, int, int, int], None],
                 config: StreamConfig, ffmpeg: str) -> None:
        self._push_au = push_au
        self._config = config
        self._ffmpeg = ffmpeg
        self._is_mac = sys.platform == "darwin"
        self._stop = threading.Event()
        self._restart = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._encoder_kind: str | None = None
        self._active_size: tuple[int, int] | None = None
        self._frames_sent = 0

    # ---- 生命周期 -------------------------------------------------------
    def start(self) -> None:
        enable_dpi_awareness()
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def apply(self, **kwargs) -> StreamConfig:
        """运行中修改画质/帧率/显示器，下一帧生效。"""
        with self._lock:
            new_cfg = self._config.with_overrides(**kwargs)
            changed = new_cfg != self._config
            self._config = new_cfg
        if changed:
            log.info("推流参数更新: %s", new_cfg)
            self._restart.set()
        return new_cfg

    @property
    def encoder_kind(self) -> str | None:
        return self._encoder_kind

    @property
    def size(self) -> tuple[int, int] | None:
        return self._active_size

    # ---- 采集线程 -------------------------------------------------------
    def _run(self) -> None:
        import mss

        sct = mss.mss()
        encoder: H264Encoder | None = None
        try:
            while not self._stop.is_set():
                with self._lock:
                    cfg = self._config

                monitors = sct.monitors
                idx = cfg.monitor if cfg.monitor < len(monitors) else 1
                mon = monitors[idx]
                size = (mon["width"], mon["height"])

                if encoder is None or self._restart.is_set() or self._active_size != size:
                    if encoder is not None:
                        encoder.close()
                        encoder = None
                    self._restart.clear()
                    kind = resolve_encoder(self._ffmpeg, cfg.encoder, self._is_mac)
                    self._encoder_kind = kind
                    self._active_size = size
                    self._frames_sent = 0
                    try:
                        encoder = H264Encoder(
                            self._ffmpeg, kind, size[0], size[1], cfg.fps,
                            cfg.quality, self._push_au,
                            bitrate_kbps=cfg.bitrate or None,
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.error("编码器启动失败: %s", exc)
                        self._stop.wait(1.0)
                        continue

                t0 = time.perf_counter()
                try:
                    raw = sct.grab(mon).bgra
                except Exception as exc:  # noqa: BLE001
                    log.warning("采集失败: %s", exc)
                    self._stop.wait(0.5)
                    continue

                if encoder is None or not encoder.write(raw):
                    if encoder is not None:
                        log.warning("编码器管道断开，重建中")
                        encoder.close()
                    encoder = None
                    self._stop.wait(0.2)
                    continue

                self._frames_sent += 1
                budget = 1.0 / max(cfg.fps, 1)
                spent = time.perf_counter() - t0
                if spent < budget:
                    self._stop.wait(budget - spent)
        finally:
            if encoder is not None:
                encoder.close()
            sct.close()
