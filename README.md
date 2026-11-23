# RemoteLink

手机 / 平板远程控制电脑，跨网可用，画质可达原画（视觉无损），延迟接近本地。

## 特性

- **原画画质**：默认 `original`（CRF 14 / 码率上限自适应），可切 `高清` / `流畅` 省流量
- **跨网访问**：不在同一网络也能用（Tailscale P2P 直连 / cloudflared 隧道，无需云服务器）
- **硬解低延迟**：H.264 + 手机 WebCodecs / MediaCodec 硬解，零延迟参数调优
- **完整控制**：鼠标（点击 / 拖动 / 右键长按）、滚轮、虚拟键盘（含中文直输）、Ctrl/Alt/Win 粘滞键、双指缩放
- **多平台 PC 端**：Windows + macOS
- **多显示器**：一键切换、多屏采集

## 目录结构

```
remotelink/
├── agent/          # PC 端服务（FastAPI + WebSocket 推流 + 输入注入 + 隧道）
├── web/            # 手机端 Web 控制界面（WebCodecs 解码 + 触控手势）
├── android/        # Android 原生 App（设备管理 + 沉浸式控制）
├── start.bat       # Windows 一键启动
├── start.sh        # macOS / Linux 一键启动
└── RemoteLink.apk  # Android 安装包（已构建）
```

## 快速开始

### 1. PC 端（被控电脑）

需要 Python 3.10+ 和 [ffmpeg](https://ffmpeg.org/download.html)（PATH 中可调用）。

```bash
# Windows
start.bat

# macOS / Linux
./start.sh
```

启动后会打印一个链接和二维码。常用参数：

```bash
start.bat --quality original --fps 60 --monitor 1   # 原画 60 帧 主屏
start.bat --password 我的口令                        # 自定义访问口令
start.bat --tunnel cloudflared                       # 跨网隧道（默认 auto）
start.bat --tunnel off                               # 仅局域网
```

### 2. 手机 / 平板端

**方式一：安装 App（推荐）**

1. 下载 `RemoteLink.apk` 安装到 Android 8.0+ 设备
2. 打开 App → 「添加设备」粘贴电脑上显示的链接（或扫码）
3. 点设备即连接，可在主页预设画质 / 帧率 / 横屏

**方式二：浏览器直接打开**

用 Chrome / Edge 打开电脑上显示的链接（需 HTTPS 才能硬解，即走隧道或 Tailscale）。

### 3. 跨网（不在同一网络）

- **Tailscale（推荐，P2P 直连、低延迟）**：PC 和手机各装 Tailscale 登录同一账号，然后用
  `https://<电脑>.ts.net/?k=<口令>` 连接即可，画面点对点不经过第三方。
- **cloudflared 隧道**：`start.bat --tunnel cloudflared` 自动生成公网地址，但延迟取决于你到
  Cloudflare 边缘节点的线路质量。

## 访问口令

服务默认生成随机口令；链接末尾的 `?k=<口令>` 即为访问令牌，泄露他人也可连接，请妥善保管。
