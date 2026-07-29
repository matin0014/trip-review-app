# Run this ONCE after extracting TripReview.zip, before launching the app.
#
# Two jobs, both of which prevent confusing failures later:
#   1. Unblock: Windows tags files that came from a network share or a zip as
#      untrusted, which blocks the app from loading its own components.
#   2. Verify: check every file against the SHA256SUMS.txt built with the app.
#      A copy over the shared drive that drops mid-transfer leaves files that
#      are the right SIZE but silently zero-filled, so nothing looks wrong until
#      the app dies at launch with an unrelated-looking error. This catches that
#      here, where the fix (re-copy) is obvious.
#
# Usage: right-click this file -> "Run with PowerShell", or from a terminal:
#            powershell -ExecutionPolicy Bypass -File .\Setup.ps1

$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "  Trip Review Tool -- setup" -ForegroundColor Cyan
Write-Host "  =========================" -ForegroundColor Cyan

# --- 1. unblock ------------------------------------------------------------
Write-Host ""
Write-Host "  [1/2] Unblocking files..."
try {
    Get-ChildItem -LiteralPath $root -Recurse -File | Unblock-File
    Write-Host "        done." -ForegroundColor Green
} catch {
    Write-Host "        WARNING: could not unblock every file: $_" -ForegroundColor Yellow
    Write-Host "        Continuing; the verify step below still matters."
}

# --- 2. verify -------------------------------------------------------------
Write-Host ""
Write-Host "  [2/2] Verifying files (this takes a moment)..."

$sumsPath = Join-Path $root "SHA256SUMS.txt"
if (-not (Test-Path -LiteralPath $sumsPath)) {
    Write-Host ""
    Write-Host "  PROBLEM: SHA256SUMS.txt is missing." -ForegroundColor Red
    Write-Host "  This folder is incomplete. Copy TripReview.zip again from the"
    Write-Host "  shared drive and extract it fresh."
    Write-Host ""
    Read-Host "  Press Enter to close"
    exit 1
}

$lines = @(Get-Content -LiteralPath $sumsPath | Where-Object { $_.Trim() })
$damaged = New-Object System.Collections.Generic.List[string]
$missing = New-Object System.Collections.Generic.List[string]
$i = 0

foreach ($line in $lines) {
    $i++
    # fixed-width parse: a SHA-256 hex digest is always 64 chars, then 2 spaces,
    # then the path (which may itself contain spaces)
    $expected = $line.Substring(0, 64)
    $rel = $line.Substring(66)
    $full = Join-Path $root $rel

    if ($i % 50 -eq 0) {
        Write-Progress -Activity "Verifying files" -Status "$i of $($lines.Count)" `
                       -PercentComplete (($i / $lines.Count) * 100)
    }

    if (-not (Test-Path -LiteralPath $full)) {
        $missing.Add($rel)
        continue
    }
    if ((Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash -ne $expected) {
        $damaged.Add($rel)
    }
}
Write-Progress -Activity "Verifying files" -Completed

# --- report ----------------------------------------------------------------
Write-Host ""
if ($damaged.Count -eq 0 -and $missing.Count -eq 0) {
    Write-Host "  READY." -ForegroundColor Green
    Write-Host "  All $($lines.Count) files verified. Double-click TripReview.exe to start."
    Write-Host ""
    Read-Host "  Press Enter to close"
    exit 0
}

Write-Host "  PROBLEM: this copy of the app is damaged." -ForegroundColor Red
Write-Host ""
if ($damaged.Count -gt 0) {
    Write-Host "  $($damaged.Count) file(s) arrived corrupted:" -ForegroundColor Red
    $damaged | Select-Object -First 8 | ForEach-Object { Write-Host "      $_" }
    if ($damaged.Count -gt 8) { Write-Host "      ... and $($damaged.Count - 8) more" }
}
if ($missing.Count -gt 0) {
    Write-Host "  $($missing.Count) file(s) are missing:" -ForegroundColor Red
    $missing | Select-Object -First 8 | ForEach-Object { Write-Host "      $_" }
    if ($missing.Count -gt 8) { Write-Host "      ... and $($missing.Count - 8) more" }
}
Write-Host ""
Write-Host "  This is a COPY problem, not a bug. To fix it:"
Write-Host "    1. Delete this folder."
Write-Host "    2. Copy TripReview.zip again from the shared drive."
Write-Host "    3. Right-click the zip -> Extract All."
Write-Host "    4. Run this Setup.ps1 again."
Write-Host ""
Write-Host "  If it fails a second time, tell Matin (the shared-drive copy"
Write-Host "  itself may be the damaged one)."
Write-Host ""
Read-Host "  Press Enter to close"
exit 1
