@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo ติดตั้งครั้งแรก...
  python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
