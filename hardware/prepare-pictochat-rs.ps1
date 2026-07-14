[CmdletBinding()]
param(
    [string] $Destination = "C:\pcx"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Repository = "https://github.com/mjwells2002/pictochat-rs.git"
$Commit = "5c75302e907148058366ccaef098fb5ac3cf2cd8"

function Invoke-Git {
    param([string[]] $Arguments)
    $output = & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git failed: git $($Arguments -join ' ')"
    }
    return $output
}

function Get-NormalizedFileText {
    param([string] $Path)
    return (Get-Content -LiteralPath $Path -Raw).Replace("`r`n", "`n").TrimEnd("`n")
}

function Get-PinnedFileText {
    param([string] $RelativePath)
    $lines = @(Invoke-Git -Arguments @("-C", $Destination, "show", "${Commit}:$RelativePath"))
    return (($lines -join "`n").Replace("`r`n", "`n").TrimEnd("`n"))
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

function Get-ExpectedPatchedText {
    param(
        [string] $RelativePath,
        [string] $Original,
        [hashtable[]] $Replacements
    )
    $result = $Original
    foreach ($replacement in $Replacements) {
        $count = Get-OccurrenceCount -Text $result -Needle $replacement.Old
        if ($count -ne $replacement.ExpectedOccurrences) {
            throw "Pinned $RelativePath did not contain the expected text exactly $($replacement.ExpectedOccurrences) time(s). Review the upstream pin and this helper."
        }
        $result = $result.Replace($replacement.Old, $replacement.New)
    }
    return $result
}

function Get-AllowedPatchedTexts {
    param(
        [string] $RelativePath,
        [string] $Original,
        [hashtable[]] $Replacements
    )
    $variants = @($Original)
    foreach ($replacement in $Replacements) {
        $nextVariants = @()
        foreach ($variant in $variants) {
            $count = Get-OccurrenceCount -Text $variant -Needle $replacement.Old
            if ($count -ne $replacement.ExpectedOccurrences) {
                throw "Could not derive strict compatibility-patch states for $RelativePath."
            }
            $nextVariants += $variant
            $nextVariants += $variant.Replace($replacement.Old, $replacement.New)
        }
        $variants = @($nextVariants)
    }
    return $variants
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required and was not found on PATH."
}

$Destination = [System.IO.Path]::GetFullPath($Destination)
$RunningOnWindows = $env:OS -eq "Windows_NT"
if ($RunningOnWindows -and $Destination.Length -gt 20) {
    throw "Use a very short source path on Windows (for example C:\pcx); ESP-IDF rejects long project paths."
}

if (-not (Test-Path -LiteralPath $Destination)) {
    New-Item -ItemType Directory -Path $Destination | Out-Null
    Invoke-Git -Arguments @("-C", $Destination, "init") | Out-Null
    Invoke-Git -Arguments @("-C", $Destination, "remote", "add", "origin", $Repository) | Out-Null
} elseif (-not (Test-Path -LiteralPath (Join-Path $Destination ".git"))) {
    $entries = @(Get-ChildItem -LiteralPath $Destination -Force)
    if ($entries.Count -ne 0) {
        throw "Destination exists and is not an empty/git directory: $Destination"
    }
    Invoke-Git -Arguments @("-C", $Destination, "init") | Out-Null
    Invoke-Git -Arguments @("-C", $Destination, "remote", "add", "origin", $Repository) | Out-Null
}

$origin = (Invoke-Git -Arguments @("-C", $Destination, "remote", "get-url", "origin")) -join ""
if ($origin.TrimEnd("/") -ne $Repository.TrimEnd("/")) {
    throw "Refusing to modify a checkout whose origin is not $Repository"
}

$status = @(Invoke-Git -Arguments @("-C", $Destination, "status", "--porcelain", "--untracked-files=all"))
$head = ""
try {
    $head = (& git -C $Destination rev-parse --verify HEAD 2>$null)
    if ($LASTEXITCODE -ne 0) {
        $head = ""
    }
} catch {
    $head = ""
}

if ($head -ne $Commit) {
    if ($status.Count -ne 0) {
        throw "The existing checkout has local changes. Preserve them or choose a new destination."
    }
    Invoke-Git -Arguments @("-C", $Destination, "fetch", "--depth", "1", "origin", $Commit) | Out-Null
    Invoke-Git -Arguments @("-C", $Destination, "checkout", "--detach", $Commit) | Out-Null
}

$actual = (Invoke-Git -Arguments @("-C", $Destination, "rev-parse", "HEAD")) -join ""
if ($actual.Trim() -ne $Commit) {
    throw "Pinned firmware checkout verification failed."
}

$patchDefinitions = @(
    @{
        Path = "Cargo.toml"
        Replacements = @(
            @{
                Old = 'simple_logger = { verison = "4.1.0", optional = true }'
                New = 'simple_logger = { version = "4.1.0", optional = true }'
                ExpectedOccurrences = 1
            }
        )
    },
    @{
        Path = "src/main.rs"
        Replacements = @(
            @{
                Old = "#![feature(cursor_remaining)]`n"
                New = ""
                ExpectedOccurrences = 1
            },
            @{
                Old = "        let mut eth_handle  = Box::into_raw(Box::new(*(ptr::null_mut() as *mut esp_eth_handle_t)));"
                New = "        // Allocate storage for the opaque handle that esp_eth_driver_install writes.`n        // Dereferencing a null pointer here (as the original code did) is UB before`n        // the ESP-IDF function ever gets a chance to initialize the handle.`n        let eth_handle = Box::into_raw(Box::new(ptr::null_mut() as esp_eth_handle_t));"
                ExpectedOccurrences = 1
            },
            @{
                Old = "        let temp_handle = Box::into_raw(Box::new(*(null_mut() as *mut temperature_sensor_handle_t)));"
                New = "        // temperature_sensor_install uses this as an out parameter.  Initialize`n        // the pointer value to null; do not dereference null to manufacture it.`n        let temp_handle = Box::into_raw(Box::new(null_mut() as temperature_sensor_handle_t));"
                ExpectedOccurrences = 1
            }
        )
    },
    @{
        Path = "src/pictochat_app_driver/pictochat_packets.rs"
        Replacements = @(
            @{
                Old = "            payload.message = cursor.remaining_slice().to_vec();"
                New = "            let position = cursor.position() as usize;`n            payload.message = cursor.get_ref().as_slice()[position..].to_vec();"
                ExpectedOccurrences = 1
            }
        )
    }
)

# ESP-IDF's component manager upgrades this tracked lock file from its v1 to v2
# serialization during a build. Accept that one exact generated form on reruns;
# arbitrary edits to the lock remain a hard error.
$knownGeneratedComponentsLock = @'
dependencies:
  espressif/mdns:
    component_hash: 31117d76cae83a6d83ffd7f035f6fdae5bd05b914fc30b641afeb208b84de19a
    dependencies:
    - name: idf
      require: private
      version: '>=5.0'
    source:
      registry_url: https://components.espressif.com/
      type: service
    version: 1.3.2
  idf:
    source:
      type: idf
    version: 5.1.4
direct_dependencies:
- espressif/mdns
manifest_hash: afdf7e0ff0d59cfab84887c889a72ec4ceffc3cfbb3c2b6d478b412893019f8c
target: esp32s3
version: 2.0.0
'@.Replace("`r`n", "`n").TrimEnd("`n")

$allowedStatus = @{}
$filesToWrite = @()
foreach ($definition in $patchDefinitions) {
    $relativePath = $definition.Path
    $path = Join-Path $Destination $relativePath
    $original = Get-PinnedFileText -RelativePath $relativePath
    $expected = Get-ExpectedPatchedText -RelativePath $relativePath -Original $original -Replacements $definition.Replacements
    $allowedVariants = @(Get-AllowedPatchedTexts -RelativePath $relativePath -Original $original -Replacements $definition.Replacements)
    $working = Get-NormalizedFileText -Path $path

    if (@($allowedVariants | Where-Object { $_ -ceq $working }).Count -ne 1) {
        throw "$relativePath has changes beyond the exact compatibility corrections owned by this helper."
    }
    if ($working -cne $expected) {
        $filesToWrite += @{ Path = $path; Text = $expected }
    }
    $allowedStatus[$relativePath] = $true
}

$componentsLockPath = Join-Path $Destination "components_esp32s3.lock"
$pinnedComponentsLock = Get-PinnedFileText -RelativePath "components_esp32s3.lock"
$workingComponentsLock = Get-NormalizedFileText -Path $componentsLockPath
if ($workingComponentsLock -ne $pinnedComponentsLock -and $workingComponentsLock -ne $knownGeneratedComponentsLock) {
    throw "components_esp32s3.lock is neither the pinned file nor the exact known ESP-IDF-generated v2 form."
}
$allowedStatus["components_esp32s3.lock"] = $true

foreach ($entry in $status) {
    if ($entry.Length -lt 4) {
        throw "Unexpected git status entry in firmware checkout: $entry"
    }
    $path = $entry.Substring(3)
    if ($entry.Substring(0, 2) -ne " M" -or -not $allowedStatus.ContainsKey($path)) {
        throw "The firmware checkout contains an unrelated, staged, or untracked change: $entry"
    }
}

foreach ($file in $filesToWrite) {
    [System.IO.File]::WriteAllText(
        $file.Path,
        $file.Text + "`n",
        [System.Text.UTF8Encoding]::new($false)
    )
}

# Validate both the content and the complete status after writing. Expected
# compatibility changes may be the only source modifications; the exact known
# component-manager lock rewrite is also tolerated as described above.
$statusAfter = @(Invoke-Git -Arguments @("-C", $Destination, "status", "--porcelain", "--untracked-files=all"))
foreach ($entry in $statusAfter) {
    if ($entry.Length -lt 4) {
        throw "Unexpected git status entry after preparing firmware: $entry"
    }
    $path = $entry.Substring(3)
    if ($entry.Substring(0, 2) -ne " M" -or -not $allowedStatus.ContainsKey($path)) {
        throw "Unexpected firmware change after preparation: $entry"
    }
}
foreach ($definition in $patchDefinitions) {
    $original = Get-PinnedFileText -RelativePath $definition.Path
    $expected = Get-ExpectedPatchedText -RelativePath $definition.Path -Original $original -Replacements $definition.Replacements
    if ((Get-NormalizedFileText -Path (Join-Path $Destination $definition.Path)) -ne $expected) {
        throw "Compatibility correction verification failed for $($definition.Path)."
    }
}

Write-Warning "The upstream repository has no licence file. This checkout is for local hardware evaluation; do not redistribute a derived image without permission."
Write-Host "Prepared pinned firmware source at $Destination"
