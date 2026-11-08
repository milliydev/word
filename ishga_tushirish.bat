@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo Virtual muhit yaratilmoqda...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
rem O'z raqamingizni yozing:
set ADMIN_PHONE=+998901234567
python server.py
pause
