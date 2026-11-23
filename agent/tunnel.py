"""内网穿透：把本机服务暴露到公网，让手机/平板在任何网络下都能连回来。

按可靠性依次尝试：
  1. cloudflared 快速隧道（若已安装或下载成功，地址形如 https://xxx.trycloudflare.com）
  2. SSH 反向隧道 localhost.run（无需安装任何东西，地址形如 https://xxx.lhr.life）
  3. SSH 反向隧道 serveo.net（地址形如 https://xxx.serveo.net）

隧道只负责把 HTTPS 流量转发到本机，画面数据不经过第三方存储；
访问仍需 PC 端生成的随机口令。
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("remotelink.tunnel")

_URL_RE = re.compile(
    r"https://[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.(?:trycloudflare\.com|lhr\.life|serveo\.net)")

_DOWNLOADS = {
    "Windows": "cloudflared-windows-amd64.exe",
    "Darwin": "cloudflared-darwin-amd64.tgz",
    "Linux": "cloudflared-linux-amd64",
}


def _bin_dir() -> Path:
    d = Path(__file__).resolve().parents[1] / "bin"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _no_window() -> dict:
    if platform.system() == "Windows":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def _local_cloudflared() -> Path | None:
    found = shutil.which("cloudflared")
    if found:
        return Path(found)
    exe = _bin_dir() / ("cloudflared.exe" if platform.system() == "Windows" else "cloudflared")
    if exe.exists() and exe.stat().st_size > 5_000_000:
        return exe
    return None


def download_cloudflared(timeout: float = 180.0) -> Path | None:
    """下载 cloudflared（网络受限时可能失败，失败会退回 SSH 隧道）。"""
    system = platform.system()
    name = _DOWNLOADS.get(system)
    if not name:
        return None
    target = _bin_dir() / ("cloudflared.exe" if system == "Windows" else "cloudflared")
    url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/{name}"
    tmp = _bin_dir() / (name + ".part")
    log.info("正在下载 cloudflared（55MB，网络慢可跳过，会自动改用 SSH 隧道）…")
    try:
        start = time.time()
        with urllib.request.urlopen(url, timeout=30) as resp, open(tmp, "wb") as out:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                if time.time() - start > timeout:
                    raise TimeoutError("下载超时")
        if name.endswith(".tgz"):
            import tarfile
            with tarfile.open(tmp) as tf:
                for member in tf.getmembers():
                    if member.name == "cloudflared":
                        tf.extract(member, str(_bin_dir()))
            tmp.unlink(missing_ok=True)
        else:
            tmp.replace(target)
        if system != "Windows":
            target.chmod(0o755)
        return target
    except Exception as exc:  # noqa: BLE001
        log.warning("cloudflared 下载失败：%s", exc)
        tmp.unlink(missing_ok=True)
        return None


class _ProcessTunnel:
    """子进程式隧道通用逻辑：读输出、抓公网地址、可停止。"""

    def __init__(self, port: int) -> None:
        self.port = port
        self.url: str | None = None
        self._proc: subprocess.Popen | None = None
        self._ready = threading.Event()

    def _launch(self, cmd: list[str], timeout: float) -> str | None:
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="ignore", bufsize=1, **_no_window())
        except Exception as exc:  # noqa: BLE001
            log.warning("隧道进程启动失败：%s", exc)
            return None

        def reader() -> None:
            assert self._proc and self._proc.stdout
            for line in iter(self._proc.stdout.readline, ""):
                m = _URL_RE.search(line)
                if m and not self.url:
                    self.url = m.group(0)
                    self._ready.set()

        threading.Thread(target=reader, name="tunnel-log", daemon=True).start()
        self._ready.wait(timeout)
        if not self.url:
            self.stop()
        return self.url

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                self._proc.kill()


class CloudflaredTunnel(_ProcessTunnel):
    def start(self, timeout: float = 25.0) -> str | None:
        exe = _local_cloudflared()
        if exe is None:
            return None
        log.info("使用 cloudflared 建立隧道…")
        cmd = [str(exe), "tunnel", "--url", f"http://127.0.0.1:{self.port}",
               "--no-autoupdate", "--loglevel", "info"]
        return self._launch(cmd, timeout)


class SshTunnel(_ProcessTunnel):
    """SSH 反向隧道，无需安装任何客户端（Windows 10+/macOS 自带 ssh）。"""

    PROVIDERS = {
        "localhost.run": "nokey@localhost.run",
        "serveo.net": "serveo.net",
    }

    def __init__(self, port: int, provider: str) -> None:
        super().__init__(port)
        self.provider = provider

    def start(self, timeout: float = 20.0) -> str | None:
        if not shutil.which("ssh"):
            return None
        host = self.PROVIDERS[self.provider]
        known = _bin_dir() / "known_hosts"
        log.info("使用 SSH 隧道（%s）…", self.provider)
        cmd = [
            "ssh", "-T",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"UserKnownHostsFile={known}",
            "-o", "ServerAliveInterval=20",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ConnectTimeout=12",
            "-R", f"80:127.0.0.1:{self.port}",
            host,
        ]
        return self._launch(cmd, timeout)


class Tunnel:
    """对外统一入口：自动挑选可用的隧道方式。"""

    def __init__(self, port: int, mode: str = "auto") -> None:
        self.port = port
        self.mode = mode
        self.url: str | None = None
        self._impl: _ProcessTunnel | None = None
        self.provider = ""

    def start(self) -> str | None:
        # 1) 本机已有 cloudflared：最快、最稳定
        if self.mode in ("auto", "cloudflared"):
            exe = _local_cloudflared()
            if exe is not None:
                t = CloudflaredTunnel(self.port)
                url = t.start()
                if url:
                    self._impl, self.url, self.provider = t, url, "cloudflared"
                    return url
            if self.mode == "cloudflared":
                exe = download_cloudflared()
                if exe:
                    t = CloudflaredTunnel(self.port)
                    url = t.start()
                    if url:
                        self._impl, self.url, self.provider = t, url, "cloudflared"
                        return url
                return None

        # 2) 免安装 SSH 反向隧道
        if self.mode in ("auto", "ssh"):
            for provider in SshTunnel.PROVIDERS:
                t = SshTunnel(self.port, provider)
                url = t.start()
                if url:
                    self._impl, self.url, self.provider = t, url, provider
                    return url

        # 3) 最后再尝试下载 cloudflared（网络慢时会超时放弃）
        if self.mode == "auto":
            exe = download_cloudflared()
            if exe:
                t = CloudflaredTunnel(self.port)
                url = t.start()
                if url:
                    self._impl, self.url, self.provider = t, url, "cloudflared"
                    return url

        return None

    def stop(self) -> None:
        if self._impl:
            self._impl.stop()
