@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Ambiente Python nao encontrado. Instale as dependencias conforme README.md.
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_widget.ps1"
if errorlevel 1 (
    echo Nao foi possivel iniciar. Consulte logs\launcher.log.
    pause
    exit /b 1
)
exit /b 0
