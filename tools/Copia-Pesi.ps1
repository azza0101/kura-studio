# Copia i pesi senza fonte pubblica su un altro PC raggiungibile in rete (cartella condivisa) o su un disco/chiavetta.
#   .\Copia-Pesi.ps1 -Destinazione "\\NOME-PC\condivisa\ComfyUI"        (cartella ComfyUI dell'altro PC condivisa)
#   .\Copia-Pesi.ps1 -Destinazione "E:\"                                (chiavetta: crea E:\models\...)
# Riprende da dove si e' interrotto (robocopy /Z) e controlla la dimensione a fine copia.
param(
  [Parameter(Mandatory = $true)][string]$Destinazione,
  [string]$Engine = "Z:\Comfy-PORTABLE\ComfyUI_windows_portable_nvidia-NEW\ComfyUI_windows_portable\ComfyUI"
)
$root = Split-Path -Parent $PSScriptRoot
$man  = Get-Content (Join-Path $root "installer\manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$roots = @((Join-Path $Engine "models"))
$yaml = Join-Path $Engine "extra_model_paths.yaml"
if (Test-Path $yaml) { foreach ($l in Get-Content $yaml) { if ($l -match '^\s*base_path:\s*(.+?)\s*$') { $b = $Matches[1].Trim('"', "'"); foreach ($c in @((Join-Path $b "models"), $b)) { if ((Test-Path $c) -and ($roots -notcontains $c)) { $roots += $c } } } } }

$ok = 0; $ko = 0
foreach ($m in ($man.models | Where-Object { $_.urls.Count -eq 0 })) {
  $nome = Split-Path $m.dest -Leaf
  $src = $null
  foreach ($r in $roots) { $src = Get-ChildItem -Path $r -Recurse -Filter $nome -File -ErrorAction SilentlyContinue | Where-Object { $_.Length -eq $m.size } | Select-Object -First 1; if ($src) { break } }
  if (-not $src) { Write-Host "NON TROVATO: $nome" -ForegroundColor Red; $ko++; continue }
  $destDir = Join-Path $Destinazione ("models\" + ((Split-Path $m.dest -Parent) -replace '/', '\'))
  New-Item -ItemType Directory -Force -Path $destDir | Out-Null
  Write-Host ("Copio {0} ({1:N2} GB) -> {2}" -f $nome, ($m.size / 1GB), $destDir) -ForegroundColor Cyan
  robocopy $src.DirectoryName $destDir $nome /Z /R:5 /W:5 /NP /NDL | Out-Null
  $dst = Join-Path $destDir $nome
  if ((Test-Path $dst) -and ((Get-Item $dst).Length -eq $m.size)) { Write-Host "  OK" -ForegroundColor Green; $ok++ } else { Write-Host "  DIMENSIONE DIVERSA: rilancia lo script per riprendere" -ForegroundColor Red; $ko++ }
}
Write-Host "`nCopiati: $ok   Problemi: $ko"
