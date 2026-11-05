# Trova sul PC i pesi senza fonte pubblica (quelli da passare a mano all'altro PC) e ne stampa il percorso completo.
# Con -Prepara crea la cartella "pesi_da_copiare" con la stessa struttura models\... pronta da trascinare (TeamViewer, rete, chiavetta).
param(
  [string]$Engine = "Z:\Comfy-PORTABLE\ComfyUI_windows_portable_nvidia-NEW\ComfyUI_windows_portable\ComfyUI",
  [switch]$Prepara
)
$root = Split-Path -Parent $PSScriptRoot
$man  = Get-Content (Join-Path $root "installer\manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json

# cartelle models/ del motore: quella interna + quelle di extra_model_paths.yaml
$roots = @((Join-Path $Engine "models"))
$yaml = Join-Path $Engine "extra_model_paths.yaml"
if (Test-Path $yaml) {
  foreach ($l in Get-Content $yaml) { if ($l -match '^\s*base_path:\s*(.+?)\s*$') { $b = $Matches[1].Trim('"', "'"); foreach ($c in @((Join-Path $b "models"), $b)) { if ((Test-Path $c) -and ($roots -notcontains $c)) { $roots += $c } } } }
}

$manuali = $man.models | Where-Object { $_.urls.Count -eq 0 }
$out = @()
foreach ($m in $manuali) {
  $nome = Split-Path $m.dest -Leaf
  $trovato = $null
  foreach ($r in $roots) {
    $trovato = Get-ChildItem -Path $r -Recurse -Filter $nome -File -ErrorAction SilentlyContinue | Where-Object { $_.Length -eq $m.size } | Select-Object -First 1
    if ($trovato) { break }
  }
  $out += [pscustomobject]@{ Id = $m.id; Dimensione = ("{0:N2} GB" -f ($m.size / 1GB)); Percorso = $(if ($trovato) { $trovato.FullName } else { "NON TROVATO" }); Destinazione = ("models\" + ($m.dest -replace '/', '\')) }
}
$out | Format-List

if ($Prepara) {
  $dst = Join-Path $root "pesi_da_copiare"
  foreach ($o in $out) {
    if ($o.Percorso -eq "NON TROVATO") { continue }
    $target = Join-Path $dst $o.Destinazione
    New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
    if (-not (Test-Path $target)) {
      cmd /c mklink /H "`"$target`"" "`"$($o.Percorso)`"" 2>$null | Out-Null          # hard link: nessuna copia dei 22 GB (stesso disco)
      if (-not (Test-Path $target)) { Copy-Item $o.Percorso $target }                # altro disco: copia vera
    }
  }
  Write-Host "`nCartella pronta: $dst" -ForegroundColor Green
  Write-Host "Trascinala (TeamViewer > Trasferimento file) nella cartella del motore sull'altro PC: le sottocartelle models\... combaciano." -ForegroundColor Green
}
