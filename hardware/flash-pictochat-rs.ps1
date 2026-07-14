[CmdletBinding()]
param(
    [string] $Source = "C:\pcx",
    [string] $Port = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Get-Command espflash -ErrorAction SilentlyContinue)) {
    throw "espflash is required and was not found on PATH. See hardware/README.md."
}

$Source = [System.IO.Path]::GetFullPath($Source)
$elf = Join-Path $Source "target\xtensa-esp32s3-espidf\release\pictochat-rs"
if (-not (Test-Path -LiteralPath $elf)) {
    throw "Release firmware not found at $elf. Run build-pictochat-rs.ps1 first."
}

if ($Port) {
    $env:ESPFLASH_PORT = $Port
}

Write-Warning "The pinned configuration flashes QIO at 80 MHz and declares 16 MB. Confirm that the connected ESP32-S3 is an N16-class module before continuing."
# espflash discovers a project's espflash.toml from its current directory (or
# a parent), not from the ELF argument. Run inside the pinned checkout so its
# QIO/80 MHz/16 MB settings are actually applied.
Push-Location $Source
try {
    & espflash flash --monitor $elf
    if ($LASTEXITCODE -ne 0) {
        throw "espflash failed."
    }
} finally {
    Pop-Location
}
