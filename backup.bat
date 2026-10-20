@echo off
call "%~dp0_setup.bat"
cd /d "%~dp0"
.venv\Scripts\python backup.py >> data\backup.log 2>&1
