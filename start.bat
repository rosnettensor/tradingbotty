@echo off
REM Windows: double-click start.bat (or run "start.bat --simulate" for an offline demo)
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv
  .venv\Scripts\pip install -q -r requirements.txt
)
if not exist .env copy .env.example .env
start "" http://localhost:8000
.venv\Scripts\python run.py %*
pause
