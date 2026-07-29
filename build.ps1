# Build the Trip Review Tool as a one-folder Windows exe (PyInstaller).
# Usage: .\build.ps1   (from the repo root; creates .venv if missing)
# Output: dist\TripReview\TripReview.exe

$ErrorActionPreference = "Stop"

$venvPython = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment..."
    py -3.9 -m venv .venv
}

Write-Host "Installing runtime + build dependencies..."
& $venvPython -m pip install -q -r requirements.txt
& $venvPython -m pip install -q -r requirements-build.txt

Write-Host "Running PyInstaller..."
& $venvPython -m PyInstaller --noconfirm --clean trip_review.spec

# The build is ~1000 files / ~200 MB. Copying that to the shared drive as loose
# files has bitten us: a transfer that drops part-way leaves files at the right
# SIZE but zero-filled, with no error shown, and the app then dies at launch
# with a misleading numpy error. So we ship a ZIP instead (its per-entry CRC
# makes a truncated copy fail loudly at extraction) plus a SHA256 manifest that
# Setup.ps1 checks on the reviewer's machine after they extract.
$distDir = ".\dist\TripReview"

Write-Host ""
Write-Host "Adding Setup.ps1 (reviewers run it after extracting)..."
Copy-Item ".\Setup.ps1" -Destination $distDir -Force

Write-Host "Generating SHA256SUMS.txt..."
$sumsPath = Join-Path $distDir "SHA256SUMS.txt"
if (Test-Path $sumsPath) { Remove-Item $sumsPath -Force }   # never hash a stale manifest
$rootLen = (Resolve-Path $distDir).Path.Length + 1
$sums = Get-ChildItem -LiteralPath $distDir -Recurse -File | ForEach-Object {
    "$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)  $($_.FullName.Substring($rootLen))"
}
$sums | Out-File -LiteralPath $sumsPath -Encoding utf8
Write-Host "  $($sums.Count) files hashed."

Write-Host "Packing dist\TripReview.zip..."
$zipPath = ".\dist\TripReview.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

# .NET rather than Compress-Archive: much faster on a 200 MB tree, and it does
# not choke the way Compress-Archive does. Retried because antivirus routinely
# holds a transient lock on files PyInstaller has only just written.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zipFull = Join-Path (Resolve-Path ".\dist").Path "TripReview.zip"
$srcFull = (Resolve-Path $distDir).Path
$packed = $false
foreach ($attempt in 1..3) {
    try {
        [System.IO.Compression.ZipFile]::CreateFromDirectory(
            $srcFull, $zipFull,
            [System.IO.Compression.CompressionLevel]::Optimal,
            $true)   # include TripReview\ as the root folder inside the zip
        $packed = $true
        break
    } catch {
        if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
        if ($attempt -eq 3) { throw "Could not pack the zip after 3 attempts: $_" }
        Write-Host "  attempt $attempt failed (file lock?), retrying in 5s..." -ForegroundColor Yellow
        Start-Sleep -Seconds 5
    }
}

# Never report success on an unverified artifact: this whole feature exists
# because a broken copy that LOOKS fine is the expensive failure.
if (-not $packed -or -not (Test-Path $zipPath)) { throw "Zip was not produced." }
$zip = [System.IO.Compression.ZipFile]::OpenRead($zipFull)
$entryCount = $zip.Entries.Count
$zip.Dispose()
if ($entryCount -lt $sums.Count) {
    throw "Zip has $entryCount entries but the build has $($sums.Count) files."
}
Write-Host "  verified: $entryCount entries."

$zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
"$zipHash  TripReview.zip" | Out-File -LiteralPath "$zipPath.sha256" -Encoding utf8

Write-Host ""
Write-Host "Build complete." -ForegroundColor Green
Write-Host "  Local test copy : dist\TripReview\TripReview.exe"
Write-Host "  To distribute   : dist\TripReview.zip  (+ TripReview.zip.sha256)"
Write-Host ""
Write-Host "Deploy: copy BOTH files to the shared drive, then confirm the copy"
Write-Host "landed intact before telling anyone it is ready:"
Write-Host "  (Get-FileHash '<shared-drive>\TripReview.zip').Hash" -ForegroundColor Cyan
Write-Host "  expected: $zipHash" -ForegroundColor Cyan
