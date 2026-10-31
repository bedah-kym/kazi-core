@echo off
setlocal
set "ROOT=%~dp0"
if exist "%ROOT%.venv\Scripts\python.exe" (
  "%ROOT%.venv\Scripts\python.exe" "%ROOT%scripts\kazi.py" %*
) else (
  python "%ROOT%scripts\kazi.py" %*
)
