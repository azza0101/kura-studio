@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\Avvia-Kura.ps1"
if errorlevel 1 (
  echo.
  echo Avvio interrotto. Leggi il messaggio sopra.
  pause
  exit /b 1
)
