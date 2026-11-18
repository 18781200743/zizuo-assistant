@echo off
cd /d %~dp0
set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
if not exist .venv\Scripts\python.exe (
  echo 正在创建虚拟环境...
  "%PY%" -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install -U pip
  pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
) else (
  call .venv\Scripts\activate.bat
)
python main.py
pause
