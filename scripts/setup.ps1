# ============================================================
# AutoPilot - One-shot environment setup (Windows PowerShell)
# Usage: .\scripts\setup.ps1
# Steps: venv -> backend deps -> Playwright Chromium -> frontend deps
# NOTE: keep this file pure ASCII (no non-ASCII chars) so that
# Windows PowerShell 5.1 can parse it without encoding issues.
# ============================================================
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$VenVDir = Join-Path $Root "backend\.venv"

Write-Host "==> [1/4] Creating backend virtualenv: $VenVDir"
if (-not (Test-Path (Join-Path $VenVDir "Scripts\python.exe"))) {
    python -m venv $VenVDir
    if ($LASTEXITCODE -ne 0) { throw "python -m venv failed (exit $LASTEXITCODE)" }
}

$VenVPython = Join-Path $VenVDir "Scripts\python.exe"
$VenVPip = Join-Path $VenVDir "Scripts\pip.exe"
$VenVPlaywright = Join-Path $VenVDir "Scripts\playwright.exe"

Write-Host "==> [2/4] Installing backend deps (prod + dev)"
& $VenVPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed (exit $LASTEXITCODE)" }
& $VenVPip install -r (Join-Path $Root "backend\requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "requirements.txt install failed (exit $LASTEXITCODE)" }
& $VenVPip install -r (Join-Path $Root "backend\requirements-dev.txt")
if ($LASTEXITCODE -ne 0) { throw "requirements-dev.txt install failed (exit $LASTEXITCODE)" }

Write-Host "==> [3/4] Installing Playwright Chromium (revision pinned by playwright==1.56.0)"
& $VenVPlaywright install chromium
if ($LASTEXITCODE -ne 0) { throw "playwright install chromium failed (exit $LASTEXITCODE)" }

Write-Host "==> [4/4] Installing frontend deps"
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Push-Location (Join-Path $Root "frontend")
    npm ci
    $npmExit = $LASTEXITCODE
    Pop-Location
    if ($npmExit -ne 0) { throw "npm ci failed (exit $npmExit)" }
}
else {
    Write-Host "    [skip] npm not found. Run manually: cd frontend; npm ci"
}

Write-Host ""
Write-Host "============================================================"
Write-Host "AutoPilot environment is ready."
Write-Host "  Backend:   cd backend; .\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
Write-Host "  Tests:     cd backend; .\.venv\Scripts\python -m pytest"
Write-Host "  Frontend:  cd frontend; npm run dev   # http://localhost:5173"
Write-Host "  (Never run uvicorn with --reload: sandbox blocks Playwright subprocess)"
Write-Host "============================================================"
