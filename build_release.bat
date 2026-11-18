@echo off
chcp 65001 >nul
cd /d %~dp0
set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
if not exist .venv\Scripts\python.exe (
  echo 请先运行 run.bat 安装依赖
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python -m pip install -U pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
python tools\make_release.py
pause
