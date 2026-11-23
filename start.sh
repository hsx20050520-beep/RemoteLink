#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if ! python3 -c "import fastapi, mss, uvicorn" >/dev/null 2>&1; then
  echo "[1/2] 正在安装依赖 ..."
  python3 -m pip install -r requirements.txt
fi

echo "[2/2] 启动 RemoteLink ..."
exec python3 -m agent.main "$@"
