"""H.264 编码器。

通过 ffmpeg 管道接收 BGRA 原始帧，输出 Annex-B 码流，并按「访问单元(AU)」
切分，供 WebCodecs 逐帧解码（零延迟）。

设计要点：
- `-tune zerolatency` / `-bf 0` / 无前瞻：编码延迟压到最低；
- `repeat-headers=1`：每个 IDR 前都带 SPS/PPS，客户端可随时从关键帧接入；
- 原画预设使用 CRF 14 + 高码率上限，桌面文字达到视觉无损。
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Callable

log = logging.getLogger("remotelink.encoder")

# 画质预设：crf/qp 决定“有多清晰”，ceiling 是码率天花板（kbps）。
# 实际上限由自适应控制给出（见 main.py），这里只是允许的最大值。
QUALITY_PRESETS: dict[str, dict] = {
    "original": {"crf": 14, "qp": 20, "ceiling": 30000},
    "hd":       {"crf": 21, "qp": 28, "ceiling": 8000},
    "smooth":   {"crf": 28, "qp": 34, "ceiling": 2500},
}


def quality_ceiling(quality: str) -> int:
    return QUALITY_PRESETS.get(quality, QUALITY_PRESETS["original"])["ceiling"]


DEFAULT_ORDER_WINDOWS = ["nvenc", "qsv", "amf", "x264"]
DEFAULT_ORDER_DARWIN = ["videotoolbox", "x264"]

_probe_cache: dict[str, bool] = {}

_VCL_TYPES = {1, 2, 3, 4, 5}


def _nal_type(nal: bytes) -> int:
    """从 00 00 01 开头的 NAL 中取 nal_unit_type。"""
    idx = 3 if nal.startswith(b"\x00\x00\x01") else 4
    if len(nal) <= idx:
        return 0
    return nal[idx] & 0x1F


class _NALReader:
    """把字节流切成完整的 NAL 单元（含起始码）。"""

    def __init__(self) -> None:
        self._buf = b""

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        buf = self._buf
        nals: list[bytes] = []
        start = buf.find(b"\x00\x00\x01")
        while start != -1:
            nxt = buf.find(b"\x00\x00\x01", start + 3)
            if nxt == -1:
                break
            end = nxt - 1 if buf[nxt - 1] == 0 else nxt
            nals.append(buf[start:end])
            start = nxt
        self._buf = buf[start:] if start != -1 else b""
        # 防御：异常流导致缓冲区无限增长
        if len(self._buf) > 32 * 1024 * 1024:
            self._buf = b""
        return nals


class _AUAssembler:
    """把 NAL 组装成访问单元：遇到新的 VCL NAL 即认为上一帧结束。"""

    def __init__(self) -> None:
        self._cur: list[bytes] = []
        self._has_vcl = False
        self._key = False

    def push(self, nal: bytes) -> tuple[bytes, bool] | None:
        t = _nal_type(nal)
        if t == 9 and self._has_vcl:          # AUD 显式分隔
            return self._flush()
        if t in _VCL_TYPES and self._has_vcl:  # 下一帧的第一个切片
            out = self._flush()
            self._append(nal, t)
            return out
        self._append(nal, t)
        return None

    def _append(self, nal: bytes, t: int) -> None:
        self._cur.append(nal)
        if t in _VCL_TYPES:
            self._has_vcl = True
            if t == 5:
                self._key = True

    def _flush(self) -> tuple[bytes, bool]:
        au = b"".join(self._cur)
        key = self._key
        self._cur = []
        self._has_vcl = False
        self._key = False
        return au, key


def _probe_one(ffmpeg: str, kind: str) -> bool:
    if kind in _probe_cache:
        return _probe_cache[kind]
    enc = {
        "nvenc": ["-c:v", "h264_nvenc", "-preset", "p1"],
        "qsv": ["-c:v", "h264_qsv", "-preset", "veryfast"],
        "amf": ["-c:v", "h264_amf", "-quality", "speed"],
        "videotoolbox": ["-c:v", "h264_videotoolbox", "-realtime", "1"],
        "x264": ["-c:v", "libx264", "-preset", "ultrafast"],
    }.get(kind)
    if enc is None:
        return False
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", "color=c=black:s=640x360:d=1",
           *enc, "-frames:v", "3", "-f", "h264", "-"]
    ok = False
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
        ok = r.returncode == 0
        if not ok:
            log.debug("编码器 %s 不可用: %s", kind, r.stderr.decode("utf-8", "ignore")[:200])
    except Exception as exc:  # noqa: BLE001
        log.debug("编码器 %s 探测失败: %s", kind, exc)
    _probe_cache[kind] = ok
    log.info("编码器探测 %-13s %s", kind, "可用" if ok else "不可用")
    return ok


def resolve_encoder(ffmpeg: str, requested: str, is_mac: bool) -> str:
    """auto 时按平台顺序挑选第一个可用编码器。"""
    if requested != "auto":
        return requested
    order = DEFAULT_ORDER_DARWIN if is_mac else DEFAULT_ORDER_WINDOWS
    for kind in order:
        if _probe_one(ffmpeg, kind):
            return kind
    return "x264"


def _build_cmd(ffmpeg: str, kind: str, width: int, height: int, fps: int,
               quality: str, keyint: int, max_kbps: int) -> list[str]:
    q = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["original"])
    rate = f"{max_kbps}k"
    buf = f"{max(400, max_kbps // 2)}k"
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer",
           "-f", "rawvideo", "-pix_fmt", "bgra",
           "-video_size", f"{width}x{height}", "-framerate", str(fps), "-i", "-"]

    if kind == "nvenc":
        cmd += ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ull",
                "-rc", "vbr", "-cq", str(q["qp"]), "-b:v", rate,
                "-maxrate", rate, "-bufsize", buf, "-profile:v", "high",
                "-g", str(keyint), "-bf", "0", "-zerolatency", "1", "-forced-idr", "1"]
    elif kind == "qsv":
        cmd += ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality",
                str(q["crf"]), "-look_ahead", "0", "-maxrate", rate,
                "-bufsize", buf, "-profile:v", "high",
                "-g", str(keyint), "-bf", "0", "-async_depth", "1"]
    elif kind == "amf":
        cmd += ["-c:v", "h264_amf", "-usage", "ultralowlatency", "-quality", "speed",
                "-rc", "vbr_peak", "-qp_i", str(q["qp"]), "-qp_p", str(q["qp"]),
                "-maxrate", rate, "-bufsize", buf,
                "-profile:v", "high", "-g", str(keyint), "-bf", "0"]
    elif kind == "videotoolbox":
        cmd += ["-c:v", "h264_videotoolbox", "-realtime", "1", "-prio_speed", "1",
                "-allow_sw", "1", "-b:v", rate, "-maxrate", rate,
                "-bufsize", buf, "-profile:v", "high", "-g", str(keyint), "-bf", "0"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-profile:v", "high", "-bf", "0",
                "-g", str(keyint), "-keyint_min", str(keyint), "-sc_threshold", "0",
                "-crf", str(q["crf"]), "-maxrate", rate, "-bufsize", buf,
                "-x264-params", "repeat-headers=1:sync-lookahead=0:rc-lookahead=0"]

    cmd += ["-flush_packets", "1", "-f", "h264", "-"]
    return cmd


class H264Encoder:
    """ffmpeg 子进程封装：write() 输入原始帧，回调输出访问单元。"""

    def __init__(self, ffmpeg: str, kind: str, width: int, height: int, fps: int,
                 quality: str, on_au: Callable[[bytes, bool, int, int, int], None],
                 bitrate_kbps: int | None = None, keyframe_seconds: float = 4.0) -> None:
        self.ffmpeg = ffmpeg
        self.kind = kind
        self.width = width
        self.height = height
        self.fps = fps
        self.quality = quality
        self.bitrate_kbps = bitrate_kbps or quality_ceiling(quality)
        self._on_au = on_au
        self._keyint = max(1, int(fps * keyframe_seconds))
        self._t0 = time.monotonic()
        self._nals = _NALReader()
        self._au = _AUAssembler()
        self._closed = False
        self._lock = threading.Lock()

        cmd = _build_cmd(ffmpeg, kind, width, height, fps, quality,
                         self._keyint, self.bitrate_kbps)
        log.info("启动编码器 %s %dx%d@%d quality=%s 码率上限=%dkbps",
                 kind, width, height, fps, quality, self.bitrate_kbps)
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._reader = threading.Thread(target=self._read_loop, name="encoder-read", daemon=True)
        self._reader.start()
        self._stderr = threading.Thread(target=self._drain_stderr, name="encoder-err", daemon=True)
        self._stderr.start()

    # ---- 对外接口 -------------------------------------------------------
    def write(self, frame: bytes) -> bool:
        with self._lock:
            if self._closed or self.proc.poll() is not None:
                return False
            try:
                self.proc.stdin.write(frame)  # type: ignore[union-attr]
                return True
            except (BrokenPipeError, ValueError, OSError):
                return False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    # ---- 内部 -----------------------------------------------------------
    def _read_loop(self) -> None:
        stdout = self.proc.stdout
        assert stdout is not None
        try:
            while True:
                chunk = stdout.read(65536)
                if not chunk:
                    break
                for nal in self._nals.feed(chunk):
                    result = self._au.push(nal)
                    if result:
                        au, key = result
                        ts = int((time.monotonic() - self._t0) * 1000)
                        self._on_au(au, key, self.width, self.height, ts)
        except Exception as exc:  # noqa: BLE001
            log.debug("编码输出线程结束: %s", exc)
        finally:
            # 收尾：把残留的最后一帧吐出去
            try:
                tail = self._au._flush()  # noqa: SLF001
                if tail[0]:
                    ts = int((time.monotonic() - self._t0) * 1000)
                    self._on_au(tail[0], tail[1], self.width, self.height, ts)
            except Exception:  # noqa: BLE001
                pass

    def _drain_stderr(self) -> None:
        stderr = self.proc.stderr
        assert stderr is not None
        for line in iter(stderr.readline, b""):
            text = line.decode("utf-8", "ignore").strip()
            if text:
                log.warning("ffmpeg: %s", text)


def find_ffmpeg() -> str | None:
    import shutil
    return shutil.which("ffmpeg")
