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

Write-Host ""
Write-Host "Build complete: dist\TripReview\TripReview.exe"
