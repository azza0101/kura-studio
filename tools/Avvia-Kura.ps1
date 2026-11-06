param([int]$TimeoutSecondi = 600, [switch]$SoloDiagnosi)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$configPath = Join-Path $root 'config.json'
$logs = Join-Path $root 'logs'

function Step($message) { Write-Host "`n$message" -ForegroundColor Cyan }
function Test-Endpoint([string]$Url, [int]$Seconds = 3) {
    try { $r = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $Seconds; return $r.StatusCode -eq 200 }
    catch { return $false }
}
function Test-Listener([int]$Port) {
    try { return $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1) }
    catch { return $false }
}

try {
    $versionFile = Join-Path $root 'app\VERSION'
    $version = if (Test-Path -LiteralPath $versionFile) { (Get-Content -LiteralPath $versionFile -Raw).Trim() } else { 'sconosciuta' }
    Write-Host "`n=== Kura Studio $version - avvio guidato ===" -ForegroundColor Green
    Step '[1/6] Controllo la configurazione'
    if (-not (Test-Path -LiteralPath $configPath)) {
        if ($SoloDiagnosi) { throw 'config.json non trovato. Avvia Installa.bat.' }
        Write-Host 'Prima configurazione: apro Installa.bat.'
        & (Join-Path $root 'Installa.bat')
        if (-not (Test-Path -LiteralPath $configPath)) { throw 'Configurazione non completata.' }
    }
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $engine = [string]$config.engine_dir
    $engineUrl = if ($config.engine_url) { ([string]$config.engine_url).TrimEnd('/') } else { 'http://127.0.0.1:8188' }
    $port = if ($config.port) { [int]$config.port } else { 7860 }
    if (-not $engine -or -not (Test-Path -LiteralPath (Join-Path $engine 'main.py') -PathType Leaf)) { throw 'Cartella ComfyUI non valida: main.py non trovato. Riapri Installa.bat e scegli la cartella corretta.' }
    $uri = [uri]$engineUrl
    if ($uri.Scheme -notin @('http','https')) { throw 'Indirizzo motore non valido in config.json.' }
    $appUrl = "http://127.0.0.1:$port"
    Write-Host "ComfyUI: $engine"
    Write-Host "Indirizzo motore: $engineUrl"
    Write-Host "Indirizzo app: $appUrl"

    Step '[2/6] Controllo Python e i componenti dellapp'
    $embedded = Join-Path (Split-Path $engine -Parent) 'python_embeded\python.exe'
    $venv = Join-Path $engine '.venv\Scripts\python.exe'
    $python = if (Test-Path -LiteralPath $embedded) { $embedded } elseif (Test-Path -LiteralPath $venv) { $venv } else { $cmd = Get-Command python -ErrorAction SilentlyContinue; if ($cmd) { $cmd.Source } else { $null } }
    if (-not $python) { throw 'Python non trovato nel portable, nella .venv o nel PATH.' }
    Write-Host "Python: $python"
    & $python --version
    if ($LASTEXITCODE -ne 0) { throw 'Python non si avvia.' }
    & $python -c 'import aiohttp'
    if ($LASTEXITCODE -ne 0) { throw 'Manca aiohttp nel Python selezionato. Controlla linstallazione di ComfyUI.' }
    Write-Host 'aiohttp disponibile.'
    if ($SoloDiagnosi) {
        Write-Host 'Verifica CUDA di PyTorch (True = disponibile):'
        & $python -c 'import torch; print(torch.cuda.is_available())'
        if ($LASTEXITCODE -ne 0) { Write-Host 'Impossibile verificare PyTorch.' -ForegroundColor Yellow }
        $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
        if ($smi) { Write-Host 'Stato NVIDIA:'; & $smi.Source }
        else { Write-Host 'nvidia-smi non trovato nel PATH.' -ForegroundColor Yellow }
    }

    Step '[3/6] Controllo il motore'
    if (Test-Endpoint "$engineUrl/system_stats") { Write-Host 'ComfyUI risponde gia.' -ForegroundColor Green }
    elseif ($SoloDiagnosi) { Write-Host 'ComfyUI non risponde; non avvio processi in modalita diagnostica.' -ForegroundColor Yellow }
    else {
        if ($uri.Host -notin @('127.0.0.1','localhost','::1')) { throw 'Il motore remoto non risponde. Avvialo sul computer remoto e riprova.' }
        if (Test-Listener $uri.Port) { throw "La porta $($uri.Port) e occupata ma non risponde come ComfyUI. Controlla il processo che la usa." }
        New-Item -ItemType Directory -Path $logs -Force | Out-Null
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $outLog = Join-Path $logs "comfy-$stamp.log"
        $errLog = Join-Path $logs "comfy-$stamp-error.log"
        Write-Host "Avvio ComfyUI. Log: $outLog"
        Write-Host "Log errori: $errLog"
        $env:PYTHONUNBUFFERED = '1'
        $arguments = @('-s', ('"{0}"' -f (Join-Path $engine 'main.py')), '--listen', '127.0.0.1', '--port', [string]$uri.Port, '--disable-auto-launch')
        $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $engine -PassThru -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog
        $null = $process.Handle
        $watch = [Diagnostics.Stopwatch]::StartNew()
        $next = 0
        while ($watch.Elapsed.TotalSeconds -lt $TimeoutSecondi) {
            if (Test-Endpoint "$engineUrl/system_stats" 2) { Write-Host "ComfyUI pronto dopo $([int]$watch.Elapsed.TotalSeconds) secondi." -ForegroundColor Green; break }
            $process.Refresh()
            if ($process.HasExited) {
                $process.WaitForExit()
                Write-Host "ComfyUI si e chiuso con codice $($process.ExitCode)." -ForegroundColor Red
                if (Test-Path -LiteralPath $errLog) {
                    $tail = @(Get-Content -LiteralPath $errLog -Tail 30)
                    $tail | ForEach-Object { Write-Host $_ }
                    if (($tail -join "`n") -match 'No CUDA GPUs are available|CUDA not available') {
                        Write-Host 'La GPU CUDA non risulta disponibile a ComfyUI. Controlla Windows, driver NVIDIA e nvidia-smi.' -ForegroundColor Yellow
                    }
                }
                throw "Avvio motore non riuscito. Controlla i log: $errLog e $outLog"
            }
            if ($watch.Elapsed.TotalSeconds -ge $next) { Write-Host "Attendo ComfyUI... $([int]$watch.Elapsed.TotalSeconds) s (massimo $TimeoutSecondi s)"; $next += 10 }
            Start-Sleep -Seconds 2
        }
        if (-not (Test-Endpoint "$engineUrl/system_stats" 3)) { throw "Tempo di attesa superato. Il processo puo essere ancora in avvio; controlla i log: $errLog e $outLog" }
    }

    Step '[4/6] Controllo la porta dellapp'
    if (Test-Endpoint "$appUrl/api/config" 10) {
        Write-Host 'Kura Studio risponde gia su questa porta.' -ForegroundColor Green
        if (-not $SoloDiagnosi) { Start-Process $appUrl }
        return
    }
    if (Test-Listener $port) { throw "La porta $port e occupata da un altro processo. Chiudi lapp che la usa o modifica la porta in config.json." }
    if ($SoloDiagnosi) { Write-Host 'Kura Studio non e in esecuzione sulla porta configurata.'; return }

    Step '[5/6] Avvio il server di Kura Studio'
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $appOut = Join-Path $logs "kura-$stamp.log"
    $appErr = Join-Path $logs "kura-$stamp-error.log"
    Write-Host "Log app: $appOut"
    Write-Host "Log errori: $appErr"
    $appArgs = @('-u', ('"{0}"' -f (Join-Path $root 'app\server.py')))
    $appProcess = Start-Process -FilePath $python -ArgumentList $appArgs -WorkingDirectory $root -PassThru -WindowStyle Hidden -RedirectStandardOutput $appOut -RedirectStandardError $appErr
    $null = $appProcess.Handle
    $watch = [Diagnostics.Stopwatch]::StartNew()
    while ($watch.Elapsed.TotalSeconds -lt 60) {
        if (Test-Endpoint "$appUrl/api/config" 5) { break }
        $appProcess.Refresh()
        if ($appProcess.HasExited) {
            $appProcess.WaitForExit()
            if (Test-Path -LiteralPath $appErr) { Get-Content -LiteralPath $appErr -Tail 30 }
            throw "Il server si e chiuso con codice $($appProcess.ExitCode). Log: $appErr"
        }
        Write-Host "Attendo il server dellapp... $([int]$watch.Elapsed.TotalSeconds) s"
        Start-Sleep -Seconds 2
    }
    if (-not (Test-Endpoint "$appUrl/api/config" 5)) { throw "Il server non risponde entro 60 secondi. Controlla $appErr" }
    Step '[6/6] Apro Kura Studio'
    Start-Process $appUrl
    Write-Host "Pronto: $appUrl. La finestra mostra lavvio; i log rimangono in $logs." -ForegroundColor Green
    Wait-Process -Id $appProcess.Id -ErrorAction SilentlyContinue
} catch {
    Write-Host "`nERRORE: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
