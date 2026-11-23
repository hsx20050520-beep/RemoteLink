"""RemoteLink：用手机/平板远程控制 PC（跨网络、原画画质）。

启动后：
  1. 本机起一个 Web 服务（采集 -> H.264 硬件/软件编码 -> WebSocket 推送）；
  2. 通过 cloudflared 快速隧道暴露到公网，生成一个 https 链接；
  3. 手机/平板打开链接（或 App 里粘贴链接），即可看到原画桌面并操作鼠标键盘。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import secrets
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .capture import describe_monitors, list_monitors
from .encoder import find_ffmpeg, quality_ceiling, resolve_encoder
from .input import create_input
from .session import ScreenStream, StreamConfig
from .tunnel import Tunnel

VERSION = "1.1.0"
WEB_DIR = Path(__file__).resolve().parents[1] / "web"

log = logging.getLogger("remotelink")

# 控制通道测得的公网往返时延（毫秒）与线路基准值，供码率自适应读取。
# base 是“空载时延”的估计：只有显著高于 base 才说明链路在排队。
_NET = {"rtt": 0.0, "base": 0.0}
# 码率档位（kbps）：只在档位之间切换，避免频繁重开编码器
RATE_LEVELS = [800, 1200, 1800, 2500, 3500, 5000, 7000, 10000, 14000, 20000, 30000]
START_KBPS = 1800
MIN_KBPS = 500


@dataclass
class Settings:
    token: str
    config: StreamConfig
    ffmpeg: str


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


def _apply_monitor(input_agent, monitor: int) -> None:
    monitors = list_monitors()
    idx = monitor if monitor < len(monitors) else 1
    m = monitors[idx]
    input_agent.set_rect(m["left"], m["top"], m["width"], m["height"])


def _handle_input(agent, msg: dict) -> None:
    """把客户端的输入事件注入系统（视频/控制通道共用）。"""
    try:
        t = msg.get("t")
        if t == "move":
            agent.move(float(msg["x"]), float(msg["y"]))
        elif t == "btn":
            agent.button(int(msg["b"]), bool(msg["d"]))
        elif t == "wheel":
            agent.wheel(float(msg.get("dy", 0)), float(msg.get("dx", 0)))
        elif t == "key":
            agent.key(str(msg.get("c", "")), bool(msg.get("d")))
        elif t == "text":
            agent.text(str(msg.get("s", "")))
    except (KeyError, TypeError, ValueError):
        pass


async def _run_client(ws: WebSocket, settings: Settings) -> None:
    await ws.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    ceiling = quality_ceiling(settings.config.quality)
    rate = {
        "cur": min(START_KBPS, ceiling),
        "ceiling": ceiling,
        "last": 0.0,
        "sent": 0,          # 本统计窗口发出的字节数
        "since": time.monotonic(),
    }
    tune = {"pending": 0}

    def push(au: bytes, key: bool, w: int, h: int, ts: int) -> None:
        loop.call_soon_threadsafe(_enqueue, au)

    def set_rate(target: int, why: str) -> None:
        target = max(MIN_KBPS, min(rate["ceiling"], int(target)))
        if target == rate["cur"]:
            return
        log.info("自适应码率 %d -> %d kbps (%s)", rate["cur"], target, why)
        rate["cur"] = target
        rate["last"] = time.monotonic()
        stream.apply(bitrate=target)

    def step_rate(down: bool) -> None:
        cur = rate["cur"]
        if down:
            lower = [v for v in RATE_LEVELS if v < cur]
            set_rate(lower[-1] if lower else MIN_KBPS, "链路排队")
        else:
            higher = [v for v in RATE_LEVELS if v > cur]
            if higher and higher[0] <= rate["ceiling"]:
                set_rate(higher[0], "链路空闲")

    def _enqueue(au: bytes) -> None:
        if queue.full():
            # 发不出去说明链路已经在排队：丢掉积压并立刻降一档
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            if time.monotonic() - rate["last"] > 3.0:
                step_rate(True)
        try:
            queue.put_nowait(au)
        except asyncio.QueueFull:
            pass

    settings.config = settings.config.with_overrides(bitrate=rate["cur"])
    stream = ScreenStream(push, settings.config, settings.ffmpeg)
    stream.start()

    input_agent = create_input()
    _apply_monitor(input_agent, settings.config.monitor)

    monitors = list_monitors()
    idx = settings.config.monitor if settings.config.monitor < len(monitors) else 1
    mon = monitors[idx]

    await ws.send_json({
        "t": "meta", "w": mon["width"], "h": mon["height"], "fps": settings.config.fps,
        "quality": settings.config.quality, "monitor": idx, "host": socket.gethostname(),
        "version": VERSION,
    })
    log.info("客户端已连接: %s", ws.client)

    async def sender() -> None:
        while True:
            au = await queue.get()
            await ws.send_bytes(au)
            rate["sent"] += len(au)

    async def tuner() -> None:
        """按「排队时延 + 实际吞吐」调码率，每档至少稳定 6 秒才动。"""
        while True:
            await asyncio.sleep(3)
            now = time.monotonic()
            elapsed = max(0.1, now - rate["since"])
            tput = rate["sent"] * 8 / elapsed / 1000          # kbps
            rate["sent"] = 0
            rate["since"] = now

            rtt, base = _NET["rtt"], _NET["base"]
            if rtt <= 0 or base <= 0:
                continue
            if base < 30:
                # 局域网/本机直连：按预设上限跑满原画，不必来回调整
                set_rate(rate["ceiling"], "局域网直连")
                continue
            queued = rtt - base
            if queued > 250:
                tune["pending"] = min(-1, tune["pending"] - 1)
            elif queued < 60 and tput > rate["cur"] * 0.7:
                tune["pending"] = max(1, tune["pending"] + 1)
            else:
                tune["pending"] = 0
                continue
            if abs(tune["pending"]) < 2 or now - rate["last"] < 6.0:
                continue
            step_rate(tune["pending"] < 0)
            tune["pending"] = 0

    async def receiver() -> None:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = msg.get("t")
            if t == "ping":
                await ws.send_json({"t": "pong", "ts": msg.get("ts")})
            elif t == "recover":
                stream.apply()
            elif t == "cfg":
                fps = int(msg["fps"]) if msg.get("fps") else None
                quality = msg.get("quality")
                monitor = int(msg["monitor"]) if msg.get("monitor") is not None else None
                cfg = stream.apply(fps=fps, quality=quality, monitor=monitor)
                if quality and quality != settings.config.quality:
                    rate["ceiling"] = quality_ceiling(cfg.quality)
                    settings.config = settings.config.with_overrides(quality=cfg.quality)
                    rate["cur"] = min(rate["cur"], rate["ceiling"])
                    stream.apply(bitrate=rate["cur"])
                if monitor is not None:
                    _apply_monitor(input_agent, cfg.monitor)
                    mons = list_monitors()
                    m2 = mons[cfg.monitor if cfg.monitor < len(mons) else 1]
                    await ws.send_json({"t": "meta", "w": m2["width"], "h": m2["height"],
                                        "fps": cfg.fps, "quality": cfg.quality,
                                        "monitor": cfg.monitor, "host": socket.gethostname()})
            else:
                _handle_input(input_agent, msg)

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver()),
             asyncio.create_task(tuner())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                log.debug("连接任务结束: %r", exc)
    finally:
        stream.stop()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
        log.info("客户端已断开")


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html",
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/info")
    async def info() -> JSONResponse:
        mons = list_monitors()
        return JSONResponse({
            "ok": True, "version": VERSION, "host": socket.gethostname(),
            "monitors": [{"index": i, "width": m["width"], "height": m["height"]}
                         for i, m in enumerate(mons) if i > 0],
        })

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        if ws.query_params.get("k") != settings.token:
            log.warning("拒绝未授权的连接: %s", ws.client)
            await ws.close(code=4401)
            return
        await _run_client(ws, settings)

    @app.websocket("/ctl")
    async def ctl_endpoint(ws: WebSocket) -> None:
        """独立控制通道：输入事件与心跳不排在视频数据后面，操作才跟手。"""
        if ws.query_params.get("k") != settings.token:
            await ws.close(code=4401)
            return
        await ws.accept()
        agent = create_input()
        _apply_monitor(agent, settings.config.monitor)

        async def pinger() -> None:
            while True:
                await asyncio.sleep(2)
                await ws.send_json({"t": "ping", "ts": int(time.time() * 1000)})

        ping_task = asyncio.create_task(pinger())
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("t") == "pong":
                    sent = float(msg.get("ts", 0))
                    rtt = max(0.0, time.time() * 1000 - sent)
                    _NET["rtt"] = rtt
                    base = _NET["base"]
                    if base <= 0 or rtt < base:
                        _NET["base"] = rtt
                    else:
                        # 基准值缓慢上漂，兼容线路本身发生变化
                        _NET["base"] = base + (rtt - base) * 0.02
                else:
                    _handle_input(agent, msg)
        except WebSocketDisconnect:
            pass
        finally:
            ping_task.cancel()

    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
    return app


def _print_qr(text: str) -> None:
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(text)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:  # noqa: BLE001
        pass


def _banner(port: int, token: str, public_url: str | None) -> None:
    lan = _lan_ip()
    local = f"http://127.0.0.1:{port}/?k={token}"
    lan_url = f"http://{lan}:{port}/?k={token}"
    best = f"{public_url}/?k={token}" if public_url else lan_url

    print()
    print("=" * 72)
    print("  RemoteLink 已启动 —— 手机/平板扫描二维码或打开链接即可控制本机")
    print("=" * 72)
    if public_url:
        print(f"  公网地址(任意网络可用): {public_url}/?k={token}")
    else:
        print("  公网地址: 未启用/未获取到（仅同一局域网内可用）")
    print(f"  局域网地址: {lan_url}   (局域网 http 下浏览器不支持 WebCodecs，请用公网 https)")
    print(f"  本机自测  : {local}")
    print(f"  访问口令  : {token}")
    print(f"  显示器    :\n{describe_monitors()}")
    print("-" * 72)
    _print_qr(best)
    print("=" * 72)
    print("  按 Ctrl+C 退出\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RemoteLink PC 端")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--password", default=None, help="固定访问口令（默认随机生成）")
    p.add_argument("--monitor", type=int, default=1, help="显示器编号（0=全部合并）")
    p.add_argument("--fps", type=int, default=30, help="帧率：60/30/15")
    p.add_argument("--quality", default="original", choices=["original", "hd", "smooth"])
    p.add_argument("--encoder", default="auto",
                   choices=["auto", "nvenc", "qsv", "amf", "videotoolbox", "x264"])
    p.add_argument("--tunnel", default="auto", choices=["auto", "cloudflared", "ssh", "off"],
                   help="跨网隧道方式：auto/cloudflared/ssh/off")
    p.add_argument("--public-url", default=None,
                   help="使用已有的隧道地址（frp/ngrok），如 https://abc.example.com")
    p.add_argument("--list-monitors", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")

    if args.list_monitors:
        print(describe_monitors())
        return

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("未找到 ffmpeg，请先安装并加入 PATH（Windows: winget install ffmpeg）")
        sys.exit(1)

    is_mac = sys.platform == "darwin"
    kind = resolve_encoder(ffmpeg, args.encoder, is_mac)
    log.info("使用编码器: %s", kind)

    token = args.password or secrets.token_urlsafe(9)
    settings = Settings(
        token=token,
        config=StreamConfig(monitor=args.monitor, fps=args.fps,
                            quality=args.quality, encoder=kind),
        ffmpeg=ffmpeg,
    )

    public_url = args.public_url.rstrip("/") if args.public_url else None
    tunnel: Tunnel | None = None
    if args.tunnel != "off" and not public_url:
        log.info("正在建立公网隧道 ...")
        tunnel = Tunnel(args.port, args.tunnel)
        public_url = tunnel.start()
        if not public_url:
            log.warning("隧道建立失败，本次仅局域网可用。"
                        "可手动安装 cloudflared 后重试，或用 --public-url 指定 frp/ngrok 地址")

    _banner(args.port, token, public_url)
    app = create_app(settings)
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning",
                    ws_ping_interval=20, ws_ping_timeout=20)
    finally:
        if tunnel:
            tunnel.stop()


if __name__ == "__main__":
    main()
