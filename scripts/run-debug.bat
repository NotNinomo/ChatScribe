@echo off
rem Run ChatScribe from source with a console showing the log.
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m chatscribe
pause
