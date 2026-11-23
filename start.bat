@echo off
chcp 65001 >nul
cd /d "%~dp0"

python -c "import fastapi, mss, uvicorn" >nul 2>&1
if errorlevel 1 (
  echo [1/2] 正在安装依赖 ...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo 依赖安装失败，请检查 Python 与网络。
    pause
    exit /b 1
  )
)

echo [2/2] 启动 RemoteLink ...
python -m agent.main %*
pause
