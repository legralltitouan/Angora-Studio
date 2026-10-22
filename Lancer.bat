@echo off
rem AutoShibi Studio 2 : double-clique sur ce fichier pour ouvrir l'interface.
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python est introuvable. Installe-le depuis python.org ^(coche "Add to PATH"^), puis relance.
    pause
    exit /b 1
)

rem Dependances : installees automatiquement au premier lancement si besoin.
python -c "import google.genai, dotenv, psutil" >nul 2>nul
if %errorlevel% neq 0 (
    echo Installation des dependances Python ^(premier lancement^)...
    python -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo L'installation des dependances a echoue. Verifie ta connexion puis relance.
        pause
        exit /b 1
    )
)

where ffmpeg >nul 2>nul
if %errorlevel% neq 0 (
    echo ATTENTION : ffmpeg est introuvable. Le logiciel s'ouvrira quand meme mais les montages
    echo ont besoin de ffmpeg. Installe-le avec :  winget install ffmpeg
    echo.
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw app.py
    exit /b
)
python app.py
if errorlevel 1 pause
