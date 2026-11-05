@echo off
setlocal
cd /d "%~dp0"
set PY=Z:\Comfy-PORTABLE\ComfyUI_windows_portable_nvidia-NEW\ComfyUI_windows_portable\python_embeded\python.exe
set ENGINE_DIR=Z:\Comfy-PORTABLE\ComfyUI_windows_portable_nvidia-NEW\ComfyUI_windows_portable

rem --- motore sullo sfondo: se non risponde lo avvia (nascosto)
powershell -NoProfile -Command "try{(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8188/system_stats -TimeoutSec 2)|Out-Null;exit 0}catch{exit 1}"
if errorlevel 1 (
  echo Avvio del motore...
  start "" /min cmd /c "cd /d %ENGINE_DIR% && python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --listen 127.0.0.1 --port 8188 --disable-auto-launch"
  :wait
  timeout /t 3 /nobreak >nul
  powershell -NoProfile -Command "try{(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8188/system_stats -TimeoutSec 2)|Out-Null;exit 0}catch{exit 1}"
  if errorlevel 1 goto wait
)
echo Apro Kura Studio...
start "" http://127.0.0.1:7860
"%PY%" app\server.py
