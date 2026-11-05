@echo off
rem Build dist\ChatScribe\ChatScribe.exe (add --no-model for a small build, --zip for a zip).
cd /d "%~dp0.."
".venv\Scripts\python.exe" tools\build_exe.py %*
