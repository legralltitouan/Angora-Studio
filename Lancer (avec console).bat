@echo off
rem AutoShibi Studio 2 avec la console visible : utile pour voir les erreurs en cas de souci.
chcp 65001 >nul
cd /d "%~dp0"
python -c "import google.genai, dotenv, psutil" >nul 2>nul
if %errorlevel% neq 0 python -m pip install -r requirements.txt
python app.py
pause
