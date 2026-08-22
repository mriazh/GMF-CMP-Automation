param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location -LiteralPath $RepoRoot

$SpecFile = Join-Path $RepoRoot "telkomsel_cmp_automation.spec"
$DistDir = Join-Path $RepoRoot "dist"
$ExpectedExeDir = Join-Path $DistDir "Telkomsel-CMP-Automation"
$ExpectedExe = Join-Path $ExpectedExeDir "Telkomsel-CMP-Automation.exe"

function Get-PyInstaller {
    $Candidate = Join-Path $RepoRoot ".venv\Scripts\pyinstaller.exe"
    if (Test-Path -LiteralPath $Candidate) {
        return $Candidate
    }
    $Found = Get-Command pyinstaller -ErrorAction SilentlyContinue
    if ($Found) {
        return $Found.Source
    }
    return $null
}

$PyInstallerPath = Get-PyInstaller
if (-not $PyInstallerPath) {
    Write-Host "Error: pyinstaller is not available. Install it in the project venv or on PATH." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath $SpecFile)) {
    Write-Host "Error: spec file is missing at $SpecFile." -ForegroundColor Red
    exit 1
}

if (Test-Path (Join-Path $RepoRoot "build")) { Remove-Item -Recurse -Force (Join-Path $RepoRoot "build") }
if (Test-Path (Join-Path $RepoRoot "dist")) { Remove-Item -Recurse -Force (Join-Path $RepoRoot "dist") }

Write-Host "Building Telkomsel-CMP-Automation.exe using $SpecFile..." -ForegroundColor Cyan
& $PyInstallerPath -y $SpecFile

if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed with exit code $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}

if (-not (Test-Path -LiteralPath $ExpectedExe)) {
    Write-Host "Error: Expected executable not found at $ExpectedExe." -ForegroundColor Red
    exit 1
}
Write-Host "Build verified. Executable found at $ExpectedExe" -ForegroundColor Green
