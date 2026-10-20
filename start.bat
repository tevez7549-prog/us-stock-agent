@echo off
call "%~dp0_setup.bat"
cd /d "%~dp0"
start "" http://localhost:8000
.venv\Scripts\python server.py
pause
