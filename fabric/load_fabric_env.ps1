# Dot-source this from the repo root to load fabric/.fabric.env into the
# current PowerShell session's environment variables:
#
#   . .\fabric\load_fabric_env.ps1
#
# Then run the simulator/API in the SAME terminal session so it inherits
# these env vars.

$envFile = Join-Path $PSScriptRoot ".fabric.env"
if (-not (Test-Path $envFile)) {
    Write-Host "No fabric/.fabric.env found. Copy fabric/.fabric.env.example to fabric/.fabric.env and fill in your connection strings first." -ForegroundColor Yellow
    return
}

$loaded = @()
Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    $key = $line.Substring(0, $idx).Trim()
    $value = $line.Substring($idx + 1).Trim()
    if ($value -eq "") { return }
    [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
    $loaded += $key
}

if ($loaded.Count -eq 0) {
    Write-Host "fabric/.fabric.env has no filled-in connection strings yet." -ForegroundColor Yellow
} else {
    Write-Host "Loaded Fabric Eventstream connection strings for: $($loaded -join ', ')" -ForegroundColor Green
}
