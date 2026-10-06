<#
.SYNOPSIS
    Installs the Line Doctor mod into the Transport Fever 3 user data folder.

.PARAMETER Target
    "mods" (default): <userdata>\local\mods          (user mods)
    "staging":        <userdata>\local\staging_area  (mods being prepared for upload)

.PARAMETER Link
    Create a directory junction instead of copying, so edits in the repo are live after a game restart.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Link
#>
param(
    [ValidateSet("mods", "staging")] [string] $Target = "mods",
    [switch] $Link,
    [string] $UserData
)

$ErrorActionPreference = "Stop"
$modId = "olrick_line_doctor_1"
$source = Join-Path $PSScriptRoot "..\mod\$modId" | Resolve-Path

if (-not $UserData) {
    $candidates = Get-ChildItem "${env:ProgramFiles(x86)}\Steam\userdata\*\3493540\local" -Directory -ErrorAction SilentlyContinue
    if (-not $candidates) { throw "TF3 user data folder not found, pass -UserData <...\3493540\local>" }
    $UserData = ($candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}

$folder = if ($Target -eq "staging") { "staging_area" } else { "mods" }
$destRoot = Join-Path $UserData $folder
$dest = Join-Path $destRoot $modId
New-Item -ItemType Directory -Force $destRoot | Out-Null

if (Test-Path $dest) {
    $item = Get-Item $dest -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { $item.Delete() }
    else { Remove-Item -Recurse -Force $dest }
}

if ($Link) {
    New-Item -ItemType Junction -Path $dest -Target $source | Out-Null
    Write-Host "Linked  $dest -> $source"
} else {
    Copy-Item -Recurse $source $dest
    Write-Host "Copied  $source -> $dest"
}

# folder where the mod writes export.json / history.jsonl
New-Item -ItemType Directory -Force (Join-Path $UserData "line_doctor") | Out-Null
Write-Host "Export folder: $(Join-Path $UserData 'line_doctor')"
