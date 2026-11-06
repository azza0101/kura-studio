@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem --- legge le cartelle da config.json (scritto da Installa.bat); se manca, lancia la configurazione
if not exist config.json (
  echo Prima configurazione...
  call "%~dp0Installa.bat"
)
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "(Get-Content config.json -Raw | ConvertFrom-Json).engine_dir"`) do set ENGINE_DIR=%%i
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "$j=Get-Content config.json -Raw | ConvertFrom-Json; if($j.port){$j.port}else{7860}"`) do set PORT=%%i
if not defined ENGINE_DIR (
  echo Cartella del motore non impostata: lancio la configurazione.
  call "%~dp0Installa.bat"
  goto :eof
)
for %%i in ("%ENGINE_DIR%\..") do set PARENT=%%~fi
set PY=%PARENT%\python_embeded\python.exe
if not exist "%PY%" set PY=python

rem --- motore sullo sfondo: se non risponde lo avvia (nascosto)
powershell -NoProfile -Command "try{(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8188/system_stats -TimeoutSec 2)|Out-Null;exit 0}catch{exit 1}"
if errorlevel 1 (
  echo Avvio del motore...
  start "" /min cmd /c ""%PY%" -s "%ENGINE_DIR%\main.py" --listen 127.0.0.1 --port 8188 --disable-auto-launch"
  :wait
  timeout /t 3 /nobreak >nul
  powershell -NoProfile -Command "try{(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8188/system_stats -TimeoutSec 2)|Out-Null;exit 0}catch{exit 1}"
  if errorlevel 1 goto wait
)
echo Apro Kura Studio...
start "" http://127.0.0.1:%PORT%
"%PY%" app\server.py
