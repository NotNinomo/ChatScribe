@echo off
rem Create the Python environment for running ChatScribe from source (Python 3.10+).
cd /d "%~dp0.."
python -m venv .venv || (echo Python 3.10+ is required: https://www.python.org/downloads/ & exit /b 1)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt || exit /b 1
if not exist "lexicon\custom.txt" copy "lexicon\custom.example.txt" "lexicon\custom.txt" >nul
echo.
echo Done. Start with scripts\run.bat  (the speech model, ~1.6 GB, downloads on first start)
