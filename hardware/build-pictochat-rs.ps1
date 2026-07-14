[CmdletBinding()]
param(
    [string] $Source = "C:\pcx",
    [string] $EspIdfToolsDir = "C:\e"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ExpectedRustVersion = "rustc 1.95.0-nightly (95e5bda86 2026-04-15) (1.95.0.0)"

function Get-NormalizedFileText {
    param([string] $Path)
    return (Get-Content -LiteralPath $Path -Raw).Replace("`r`n", "`n")
}

function Get-OccurrenceCount {
    param(
        [string] $Text,
        [string] $Needle
    )
    $count = 0
    $offset = 0
    while (($offset = $Text.IndexOf($Needle, $offset, [System.StringComparison]::Ordinal)) -ge 0) {
        $count++
        $offset += $Needle.Length
    }
    return $count
}

function Get-TextSha256 {
    param([string] $Text)
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Set-StrictCompatibilityPatch {
    param(
        [string] $Path,
        [hashtable[]] $Replacements,
        [string] $OriginalSha256,
        [string] $PatchedSha256
    )

    $working = Get-NormalizedFileText -Path $Path
    $canonicalOriginal = $working
    foreach ($replacement in $Replacements) {
        $oldCount = Get-OccurrenceCount -Text $canonicalOriginal -Needle $replacement.Old
        $newCount = Get-OccurrenceCount -Text $canonicalOriginal -Needle $replacement.New
        $expected = $replacement.ExpectedOccurrences

        if ($oldCount -eq $expected -and $newCount -eq 0) {
            continue
        }
        if ($oldCount -eq 0 -and $newCount -eq $expected) {
            $canonicalOriginal = $canonicalOriginal.Replace($replacement.New, $replacement.Old)
            continue
        }
        throw "$(Split-Path -Leaf $Path) is not in the exact original/patched state expected for esp-idf-svc 0.49.0."
    }

    if ((Get-TextSha256 -Text $canonicalOriginal) -ne $OriginalSha256) {
        throw "Refusing to patch $Path because it contains changes outside the reviewed compatibility edits."
    }

    $patched = $canonicalOriginal
    foreach ($replacement in $Replacements) {
        $patched = $patched.Replace($replacement.Old, $replacement.New)
    }
    if ((Get-TextSha256 -Text $patched) -ne $PatchedSha256) {
        throw "Internal compatibility-patch hash verification failed for $Path."
    }

    if ($working -ne $patched) {
        [System.IO.File]::WriteAllText(
            $Path,
            $patched,
            [System.Text.UTF8Encoding]::new($false)
        )
    }
}

$Source = [System.IO.Path]::GetFullPath($Source)
$EspIdfToolsDir = [System.IO.Path]::GetFullPath($EspIdfToolsDir)
$RunningOnWindows = $env:OS -eq "Windows_NT"
if (-not (Test-Path -LiteralPath (Join-Path $Source "Cargo.toml"))) {
    throw "Firmware source not found at $Source. Run prepare-pictochat-rs.ps1 first."
}
if (-not (Test-Path -LiteralPath (Join-Path $Source "sdkconfig.defaults"))) {
    throw "Pinned sdkconfig.defaults not found at $Source. Run prepare-pictochat-rs.ps1 again."
}
if ($RunningOnWindows -and $Source.Length -gt 20) {
    throw "Use a very short source path on Windows (for example C:\pcx); ESP-IDF rejects long project paths."
}
if ($RunningOnWindows -and $EspIdfToolsDir.Length -gt 20) {
    throw "Use a very short ESP-IDF tools path on Windows (for example C:\e)."
}
if (-not [string]::IsNullOrWhiteSpace($env:CARGO_TARGET_DIR)) {
    throw "CARGO_TARGET_DIR is set to '$env:CARGO_TARGET_DIR'. Clear it so the build and flash helpers agree on the pinned checkout's target directory."
}
foreach ($command in @("cargo", "rustc", "ldproxy")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "$command is required and was not found on PATH. See hardware/README.md."
    }
}

# Name the Espressif toolchain explicitly. Checking plain rustc outside the
# checkout can otherwise inspect the user's stable default rather than the
# `esp` channel selected by the firmware's rust-toolchain.toml.
$rustVersion = (& rustc +esp --version) -join ""
if ($LASTEXITCODE -ne 0 -or $rustVersion -cne $ExpectedRustVersion) {
    throw "This pinned firmware requires the exact tested Espressif toolchain '$ExpectedRustVersion', but 'rustc +esp --version' returned '$rustVersion'. See hardware/README.md."
}

# ESP-IDF 5.1's generated compiler probes can lose Newlib include paths when
# its Windows tools directory is long. Keep both project and tool paths short.
$env:ESP_IDF_TOOLS_INSTALL_DIR = "custom:$EspIdfToolsDir"
$env:IDF_TOOLS_PATH = $EspIdfToolsDir
$env:ESP_IDF_SDKCONFIG_DEFAULTS = Join-Path $Source "sdkconfig.defaults"

Push-Location $Source
try {
    & cargo +esp fetch --locked
    if ($LASTEXITCODE -ne 0) {
        throw "Fetching the pinned firmware dependency graph failed."
    }

    $metadataText = (& cargo +esp metadata --locked --format-version 1) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        throw "Reading the pinned Cargo dependency graph failed."
    }
    $metadata = $metadataText | ConvertFrom-Json
    $expectedTargetDirectory = [System.IO.Path]::GetFullPath((Join-Path $Source "target"))
    $actualTargetDirectory = [System.IO.Path]::GetFullPath($metadata.target_directory)
    if ($actualTargetDirectory.TrimEnd("\", "/") -cne $expectedTargetDirectory.TrimEnd("\", "/")) {
        throw "Cargo resolved target_directory '$actualTargetDirectory', but this workflow requires '$expectedTargetDirectory' so the flash helper uses the image just built. Remove any target-dir override."
    }
    $svcPackages = @($metadata.packages | Where-Object {
        $_.name -eq "esp-idf-svc" -and $_.version -eq "0.49.0"
    })
    if ($svcPackages.Count -ne 1) {
        throw "Expected exactly one esp-idf-svc 0.49.0 package in Cargo.lock, found $($svcPackages.Count)."
    }
    if (-not $svcPackages[0].source -or -not $svcPackages[0].source.StartsWith("registry+")) {
        throw "esp-idf-svc 0.49.0 did not resolve from the Cargo registry; refusing to patch an unexpected source."
    }

    $svcRoot = Split-Path -Parent $svcPackages[0].manifest_path
    $cstrPath = Join-Path $svcRoot "src\private\cstr.rs"
    $tlsPath = Join-Path $svcRoot "src\tls.rs"
    if (-not (Test-Path -LiteralPath $cstrPath) -or -not (Test-Path -LiteralPath $tlsPath)) {
        throw "Cargo fetched esp-idf-svc 0.49.0, but its reviewed source files were not found under $svcRoot."
    }

    # esp-idf-svc 0.49.0 hardcoded Rust i8 for C char pointers. New bindgen
    # correctly follows the target ABI and may emit u8. These exact edits mirror
    # the upstream c_char fix; hashes reject any unrelated registry-cache edit.
    Set-StrictCompatibilityPatch -Path $cstrPath `
        -OriginalSha256 "34a07187d444dd122c93dbe0826a313f69bc8b6685603378cc478331e74d799a" `
        -PatchedSha256 "c704d51973b5272ebb9fa0d2d97d3dd3e6d82c67926131bfa9cd22e60a6bb26f" `
        -Replacements @(
            @{
                Old = ") -> Result<[*const i8; N], EspError> {"
                New = ") -> Result<[*const c_char; N], EspError> {"
                ExpectedOccurrences = 1
            }
        )

    Set-StrictCompatibilityPatch -Path $tlsPath `
        -OriginalSha256 "dd1a9dc4f9e0afc77c79acd0bb7f461da9860b6a75d2b0e91606eb9e092dc863" `
        -PatchedSha256 "d97c1bbbed6f74c211239377745b7367a91f4bf21b27288d7ddfcdef28fb8b4b" `
        -Replacements @(
            @{
                Old = "mod esptls {`n    #[cfg(esp_idf_esp_tls_server_cert_select_hook)]"
                New = "mod esptls {`n    use core::ffi::c_char;`n    #[cfg(esp_idf_esp_tls_server_cert_select_hook)]"
                ExpectedOccurrences = 1
            },
            @{
                Old = "        alpn_protos: [*const i8; 10],"
                New = "        alpn_protos: [*const c_char; 10],"
                ExpectedOccurrences = 1
            },
            @{
                Old = "                        host.as_bytes().as_ptr() as *const i8,"
                New = "                        host.as_bytes().as_ptr() as *const c_char,"
                ExpectedOccurrences = 2
            },
            @{
                Old = "            unsafe { read_func(self.raw, buf.as_mut_ptr() as *mut i8, buf.len()) }"
                New = "            unsafe { read_func(self.raw, buf.as_mut_ptr() as *mut c_char, buf.len()) }"
                ExpectedOccurrences = 1
            },
            @{
                Old = "            unsafe { write_func(self.raw, buf.as_ptr() as *const i8, buf.len()) }"
                New = "            unsafe { write_func(self.raw, buf.as_ptr() as *const c_char, buf.len()) }"
                ExpectedOccurrences = 1
            }
        )

    & cargo +esp build --release --locked --features use-embuild
    if ($LASTEXITCODE -ne 0) {
        throw "Firmware build failed."
    }
} finally {
    Pop-Location
}

$elf = Join-Path $Source "target\xtensa-esp32s3-espidf\release\pictochat-rs"
if (-not (Test-Path -LiteralPath $elf)) {
    throw "Cargo succeeded but the expected ESP32-S3 release ELF was not found at $elf"
}
Write-Host "Firmware ELF: $elf"
