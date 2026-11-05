@echo off
rem Run ChatScribe from source without a console window.
cd /d "%~dp0.."
if not exist ".venv\Scripts\pythonw.exe" call scripts\setup.bat || exit /b 1
start "" ".venv\Scripts\pythonw.exe" -m chatscribe
