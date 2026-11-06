# Kura Studio - configurazione guidata.
# Chiede la cartella di ComfyUI e quella dei modelli, controlla cosa c'e' gia', scarica solo quello che manca
# (mettendolo nella cartella modelli scelta) e scrive config.json. Rilanciabile quando vuoi.
param(
  [string]$Engine,                 # cartella ComfyUI (quella con main.py) - se omessa la chiede
  [string]$Models,                 # cartella dei modelli - se omessa la chiede
  [switch]$Si,                     # non chiede conferme (per prove automatiche)
  [string]$ConfigOut               # dove scrivere config.json (default: accanto allo script)
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
if (-not $ConfigOut) { $ConfigOut = Join-Path $root "config.json" }
Add-Type -AssemblyName System.Windows.Forms | Out-Null

function Scegli-Cartella($titolo, $iniziale) {
  $d = New-Object System.Windows.Forms.FolderBrowserDialog
  $d.Description = $titolo; $d.ShowNewFolderButton = $false
  if ($iniziale -and (Test-Path $iniziale)) { $d.SelectedPath = $iniziale }
  if ($d.ShowDialog() -eq "OK") { return $d.SelectedPath } else { return $null }
}

Write-Host "`n=== Kura Studio - configurazione ===" -ForegroundColor Cyan
$cfg = if (Test-Path $ConfigOut) { Get-Content $ConfigOut -Raw -Encoding UTF8 | ConvertFrom-Json } else { [pscustomobject]@{} }
function Imposta($k, $v) { $cfg | Add-Member -NotePropertyName $k -NotePropertyValue $v -Force }

# 1) cartella del motore
if (-not $Engine) {
  Write-Host "1/3  Scegli la cartella di ComfyUI (quella che contiene main.py, di solito ...\ComfyUI)"
  $Engine = Scegli-Cartella "Cartella di ComfyUI (con main.py)" $cfg.engine_dir
}
if (-not $Engine -or -not (Test-Path (Join-Path $Engine "main.py"))) { Write-Host "main.py non trovato in '$Engine'. Interrotto." -ForegroundColor Red; exit 1 }

# 2) cartella dei modelli (se non diversa, quella interna)
if (-not $Models) {
  $def = Join-Path $Engine "models"
  Write-Host "2/3  Scegli la cartella dei MODELLI (quella con diffusion_models, loras, vae...). Annulla = $def"
  $Models = Scegli-Cartella "Cartella dei modelli (con diffusion_models, loras, vae...)" $(if ($cfg.models_dir) { $cfg.models_dir } else { $def })
  if (-not $Models) { $Models = $def }
}
if (-not (Test-Path $Models)) { New-Item -ItemType Directory -Force -Path $Models | Out-Null }
Write-Host "`n  Motore : $Engine`n  Modelli: $Models"

# python del motore
$emb = Join-Path (Split-Path $Engine -Parent) "python_embeded\python.exe"
$py = if (Test-Path $emb) { $emb } elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { $null }
if (-not $py) { Write-Host "Non trovo Python." -ForegroundColor Red; exit 1 }

# 3) scansione di quello che c'e' gia'
Write-Host "`n3/3  Controllo cosa c'e' gia'..." -ForegroundColor Cyan
& $py (Join-Path $root "installer\install.py") --engine $Engine --models $Models --check
if (-not $Si) {
  $r = Read-Host "`nScarico e installo quello che manca? [S/n]"
  if ($r -match '^[nN]') { Write-Host "Va bene, non installo nulla." ; exit 0 }
}
$mirror = @(); $m = Join-Path $root "pesi_da_copiare"; if (Test-Path $m) { $mirror += @("--mirror", $m) }
& $py (Join-Path $root "installer\install.py") --engine $Engine --models $Models @mirror
$esito = $LASTEXITCODE

# i modelli sono fuori dalla cartella del motore? Il motore deve saperlo (extra_model_paths.yaml)
$interna = (Join-Path $Engine "models")
if ($Models.TrimEnd('\') -ne $interna.TrimEnd('\')) {
  $yaml = Join-Path $Engine "extra_model_paths.yaml"
  $norm = { param($x) ($x.TrimEnd('\', '/') -replace '\\', '/').ToLower() }
  $testo = if (Test-Path $yaml) { ((Get-Content $yaml -Raw) -replace '\\', '/').ToLower() } else { "" }
  # gia' indicata se nel file compare la cartella modelli oppure la sua cartella madre (base_path)
  $gia = $testo.Contains((& $norm $Models)) -or $testo.Contains((& $norm (Split-Path $Models -Parent)))
  if (-not $gia) {
    $ok = $Si; if (-not $Si) { $ok = (Read-Host "`nLa cartella modelli e' fuori dal motore: la aggiungo a extra_model_paths.yaml (con copia di sicurezza)? [S/n]") -notmatch '^[nN]' }
    if ($ok) {
      if (Test-Path $yaml) { Copy-Item $yaml "$yaml.kura-bak" -Force }
      $yy = ($Models -replace '\\', '/')
      $blocco = "`r`nkura_studio:`r`n    base_path: $yy/`r`n    checkpoints: checkpoints`r`n    diffusion_models: diffusion_models`r`n    text_encoders: text_encoders`r`n    clip: clip`r`n    vae: vae`r`n    vae_approx: vae_approx`r`n    loras: loras`r`n"
      Add-Content -Path $yaml -Value $blocco -Encoding UTF8
      Write-Host "Aggiornato $yaml (riavvia il motore per applicarlo)." -ForegroundColor Green
    }
  }
}

# decoder: TensorRT solo se il motore ha il nodo e l'engine
$trt = (Test-Path (Join-Path $Engine "custom_nodes\ComfyUI-H3VAE_TRT")) -and ((@($Models, (Join-Path $Engine "models")) | Where-Object { Test-Path $_ } | ForEach-Object { Get-ChildItem -Path $_ -Recurse -Filter "minimax_h3_vae_decoder.engine" -File -ErrorAction SilentlyContinue } | Select-Object -First 1))
Imposta "engine_dir" $Engine
Imposta "models_dir" $Models
Imposta "engine_output_dir" (Join-Path $Engine "output")
if (-not $cfg.engine_url) { Imposta "engine_url" "http://127.0.0.1:8188" }
if (-not $cfg.port) { Imposta "port" 7860 }
Imposta "decoder" $(if ($trt) { "trt" } else { "standard" })
if (-not $cfg.video_format) { Imposta "video_format" "video/h264-mp4" }
if (-not $cfg.update_repo) { Imposta "update_repo" "azza0101/kura-studio" }
if (-not $cfg.update_branch) { Imposta "update_branch" "main" }
($cfg | ConvertTo-Json) | Set-Content -Path $ConfigOut -Encoding UTF8
Write-Host "`nConfigurazione salvata: $ConfigOut" -ForegroundColor Green
if ($esito -ne 0) { Write-Host "ATTENZIONE: qualche peso manca ancora (vedi sopra). L'app parte lo stesso e segnala cosa manca." -ForegroundColor Yellow }
else { Write-Host "Tutto pronto: avvia Kura Studio con start.bat" -ForegroundColor Green }
