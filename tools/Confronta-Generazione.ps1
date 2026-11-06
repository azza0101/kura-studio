param(
    [string]$ComfyUrl = 'http://127.0.0.1:8188',
    [string[]]$CartelleModelli = @(),
    [switch]$HashModelli,
    [string]$Destinazione = ''
)
$ErrorActionPreference = 'Stop'

function Hash-Testo([string]$testo) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($testo)))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

try {
    if (-not $Destinazione) { $Destinazione = Join-Path ([Environment]::GetFolderPath('Desktop')) ("Kura-confronto-{0}.json" -f (Get-Date -Format 'yyyyMMdd-HHmmss')) }
    Write-Host 'Leggo lo stato e la cronologia di ComfyUI...' -ForegroundColor Cyan
    $stats = Invoke-RestMethod "$ComfyUrl/system_stats" -TimeoutSec 20
    $history = Invoke-RestMethod "$ComfyUrl/history" -TimeoutSec 30
    $items = @($history.PSObject.Properties | Where-Object { $_.Value.prompt -and $_.Value.prompt.Count -ge 3 -and $_.Value.prompt[2].PSObject.Properties.Count -gt 0 })
    if (-not $items.Count) { throw 'Nessuna generazione trovata nella cronologia di ComfyUI.' }
    $index = $items.Count - 1
    if ($items.Count -gt 1) {
        Write-Host "Trovate $($items.Count) generazioni. Le ultime dieci:"
        $start = [Math]::Max(0, $items.Count - 10)
        for ($i = $start; $i -lt $items.Count; $i++) {
            $record = $items[$i].Value
            $stamp = $null
            foreach ($message in @($record.status.messages)) { if ($message[1].timestamp) { $stamp = [DateTimeOffset]::FromUnixTimeMilliseconds([long]$message[1].timestamp).LocalDateTime.ToString('yyyy-MM-dd HH:mm:ss'); break } }
            Write-Host ("[{0}] {1}  {2}  {3}" -f $i, $stamp, $record.status.status_str, $items[$i].Name)
        }
        $answer = Read-Host "Numero della generazione da confrontare (Invio = ultima: $index)"
        if ($answer -ne '') { $n = 0; if (-not [int]::TryParse($answer, [ref]$n) -or $n -lt 0 -or $n -ge $items.Count) { throw 'Numero non valido.' }; $index = $n }
    }
    $entry = $items[$index]
    $workflow = $entry.Value.prompt[2]
    $nodes = @()
    $models = @()
    $promptHashes = @()
    $referenceFiles = @()
    foreach ($node in $workflow.PSObject.Properties) {
        $class = [string]$node.Value.class_type
        $safeInputs = [ordered]@{}
        foreach ($field in $node.Value.inputs.PSObject.Properties) {
            $value = $field.Value
            if ($class -eq 'PrimitiveStringMultiline' -and $field.Name -eq 'value' -and $value -is [string]) {
                $promptHashes += [ordered]@{ nodo = $node.Name; sha256 = Hash-Testo $value; caratteri = $value.Length }
                continue
            }
            if (($field.Name -match '^(text|prompt|string|filename_prefix)$') -and $value -is [string]) {
                $promptHashes += [ordered]@{ nodo = $node.Name; sha256 = Hash-Testo $value; caratteri = $value.Length }
                continue
            }
            if ($value -is [string] -and $field.Name -notin @('unet_name','clip_name','vae_name','lora_name','decoder','encoder','image','type','device','weight_dtype','sampler_name','scheduler','attention','selection','sink_conditioning','ref_image_size','format','pix_fmt')) {
                $safeInputs[$field.Name] = '[stringa omessa]'
                continue
            }
            $safeInputs[$field.Name] = $value
            if ($field.Name -in @('unet_name','clip_name','vae_name','lora_name','decoder','encoder') -and $value -is [string] -and $value -ne 'None') {
                $models += [ordered]@{ nodo = $node.Name; tipo = $class; nome = $value; percorso = $null; byte = $null; sha256 = $null }
            }
            if ($class -eq 'LoadImage' -and $field.Name -eq 'image' -and $value -is [string]) { $referenceFiles += $value }
        }
        $nodes += [ordered]@{ id = $node.Name; tipo = $class; parametri = $safeInputs }
    }
    if (-not $CartelleModelli.Count) {
        $configPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'config.json'
        if (Test-Path -LiteralPath $configPath) {
            $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($config.models_dir) { $CartelleModelli += [string]$config.models_dir }
            if ($config.engine_dir) { $CartelleModelli += (Join-Path $config.engine_dir 'models') }
        }
    }
    $roots = @($CartelleModelli | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) } | Select-Object -Unique)
    foreach ($model in $models) {
        $filename = ($model.nome -replace '\\','/').Split('/')[-1]
        $candidates = @()
        foreach ($root in $roots) { $candidates += @(Get-ChildItem -LiteralPath $root -Filter $filename -File -Recurse -ErrorAction SilentlyContinue) }
        if ($candidates.Count -eq 1) {
            $model.percorso = $candidates[0].FullName; $model.byte = $candidates[0].Length
            if ($HashModelli) { Write-Host "Calcolo SHA-256: $filename (puo' richiedere minuti)..."; $model.sha256 = (Get-FileHash -LiteralPath $candidates[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
        } elseif ($candidates.Count -gt 1) { $model.percorso = 'Piu copie trovate: identificazione non certa'; $model.byte = @($candidates | ForEach-Object { $_.Length }) }
    }
    $references = @()
    $engineDir = if ($config -and $config.engine_dir) { [string]$config.engine_dir } else { '' }
    foreach ($ref in $referenceFiles) {
        $entryRef = [ordered]@{ nome = $ref; byte = $null; sha256 = $null }
        if ($engineDir) {
            $refPath = Join-Path (Join-Path $engineDir 'input') $ref
            if (Test-Path -LiteralPath $refPath -PathType Leaf) { $file = Get-Item -LiteralPath $refPath; $entryRef.byte = $file.Length; $entryRef.sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
        }
        $references += $entryRef
    }
    $report = [ordered]@{
        generato = (Get-Date).ToString('o'); computer = $env:COMPUTERNAME; comfy_url = $ComfyUrl
        comfy_versione = $stats.system.comfyui_version; sistema = $stats.system.os; gpu = @($stats.devices | ForEach-Object { $_.name })
        generazione_id = $entry.Name; esito = $entry.Value.status.status_str
        prompt_hash = $promptHashes; riferimento = $references; modelli = $models; workflow = $nodes
    }
    $report | ConvertTo-Json -Depth 50 | Set-Content -LiteralPath $Destinazione -Encoding UTF8
    Write-Host "`nReport salvato: $Destinazione" -ForegroundColor Green
    Write-Host 'Il report include nomi e percorsi dei file, parametri e impronte del prompt, ma non il testo del prompt.'
    if (-not $HashModelli) { Write-Host 'Per verificare anche il contenuto dei pesi, ripeti con -HashModelli: richiede tempo per i file grandi.' }
} catch {
    Write-Host "ERRORE: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

