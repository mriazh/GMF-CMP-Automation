param(
    [string]$Version = "1.0.0",
    [switch]$Force,
    [switch]$Upload
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RootDir = Split-Path -Parent $ScriptDir
Set-Location -LiteralPath $RootDir

if ([string]::IsNullOrWhiteSpace($Version) -or $Version -match '[\\/]') {
    Write-Host "Error: Version must be a non-empty release name without path separators." -ForegroundColor Red
    exit 1
}

$StagingFolder = "Telkomsel-CMP-Automation-Portable"
$StagingParent = Join-Path $RootDir "staging"
$StagingDir = Join-Path $StagingParent $StagingFolder
$ReleaseDir = Join-Path $RootDir "release"
$ZipName = "Telkomsel-CMP-Automation-v$Version-portable.zip"
$ZipPath = Join-Path $ReleaseDir $ZipName

$AllowedFiles = @(
    ".env.example",
    "README.md",
    "pyproject.toml"
)

$ForbiddenPatterns = @(
    "\.env$",
    "\.log$",
    "logs",
    "\.git",
    "__pycache__",
    "\.pyc$",
    "tests",
    "\.pytest_cache",
    "docs"
)

function Stop-Packaging {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    Write-Host "Error: $Message" -ForegroundColor Red
    if (Test-Path -LiteralPath $StagingDir) {
        Remove-Item -LiteralPath $StagingDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    exit 1
}

if (Test-Path -LiteralPath $StagingDir) {
    if ($Force) {
        Write-Host "Force specified: removing existing staging directory." -ForegroundColor Yellow
        Remove-Item -LiteralPath $StagingDir -Recurse -Force
    } elseif (@(Get-ChildItem -LiteralPath $StagingDir -Force).Count -gt 0) {
        Stop-Packaging "Staging directory already contains files. Use -Force to replace it: $StagingDir"
    }
}

if (Test-Path -LiteralPath $ZipPath) {
    if ($Force) {
        Write-Host "Force specified: removing existing release archive." -ForegroundColor Yellow
        Remove-Item -LiteralPath $ZipPath -Force
    } else {
        Stop-Packaging "Release archive already exists. Use -Force to replace it: $ZipPath"
    }
}

if (-not (Test-Path -LiteralPath $StagingParent)) {
    New-Item -ItemType Directory -Path $StagingParent | Out-Null
}
if (-not (Test-Path -LiteralPath $StagingDir)) {
    New-Item -ItemType Directory -Path $StagingDir | Out-Null
}
if (-not (Test-Path -LiteralPath $ReleaseDir)) {
    New-Item -ItemType Directory -Path $ReleaseDir | Out-Null
}

$DistDir = Join-Path $RootDir "dist\Telkomsel-CMP-Automation"
$ExePath = Join-Path $DistDir "Telkomsel-CMP-Automation.exe"
if (-not (Test-Path -LiteralPath $ExePath)) {
    Stop-Packaging "Cannot find $ExePath. Run .\scripts\build_exe.ps1 first."
}

Write-Host "Copying frozen distribution from $DistDir..." -ForegroundColor Cyan
$DistEntries = @(Get-ChildItem -LiteralPath $DistDir -Force)
foreach ($Entry in $DistEntries) {
    Copy-Item -LiteralPath $Entry.FullName -Destination $StagingDir -Recurse -Force
}

Write-Host "Populating clean staging directory with example config and documents..." -ForegroundColor Cyan
foreach ($RelativePath in $AllowedFiles) {
    $SourcePath = Join-Path $RootDir $RelativePath
    $DestinationPath = Join-Path $StagingDir $RelativePath
    if (Test-Path -LiteralPath $SourcePath) {
        Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath -Force
    }
}

# Copy config directory with example template
$ConfigStaging = Join-Path $StagingDir "config"
New-Item -ItemType Directory -Path $ConfigStaging -Force | Out-Null
$ExampleTemplate = Join-Path $RootDir "config\Daily-Data-Usage-M2M.example.xlsx"
if (Test-Path -LiteralPath $ExampleTemplate) {
    Copy-Item -LiteralPath $ExampleTemplate -Destination (Join-Path $ConfigStaging "Daily-Data-Usage-M2M.example.xlsx") -Force
}

$TarCommand = $null
foreach ($Candidate in @("$env:WINDIR\System32\tar.exe", "$env:WINDIR\Sysnative\tar.exe")) {
    if (-not [string]::IsNullOrWhiteSpace($Candidate) -and (Test-Path -LiteralPath $Candidate)) {
        $TarCommand = $Candidate
        break
    }
}
if (-not $TarCommand) {
    $FoundTar = Get-Command tar.exe -ErrorAction SilentlyContinue
    if ($FoundTar) { $TarCommand = $FoundTar.Source }
}
if (-not $TarCommand) {
    Stop-Packaging "Windows tar.exe was not found."
}

Write-Host "Creating portable ZIP with tar.exe..." -ForegroundColor Cyan
$TarOutput = & $TarCommand -a -cf $ZipPath -C $StagingParent $StagingFolder 2>&1
$TarExitCode = $LASTEXITCODE
if ($TarExitCode -ne 0) {
    Stop-Packaging "tar.exe failed while creating the ZIP archive."
}

# Clean staging directory
Remove-Item -LiteralPath $StagingDir -Recurse -Force -ErrorAction SilentlyContinue

$ArchiveSizeMB = [math]::Round((Get-Item -LiteralPath $ZipPath).Length / 1MB, 2)
Write-Host "ZIP created successfully: $ZipPath ($ArchiveSizeMB MB)" -ForegroundColor Green

if ($Upload) {
    $GhCommand = Get-Command gh.exe -ErrorAction SilentlyContinue
    if (-not $GhCommand) { $GhCommand = Get-Command gh -ErrorAction SilentlyContinue }
    if (-not $GhCommand) {
        Stop-Packaging "The GitHub CLI (gh) is required for -Upload."
    }

    Write-Host "Checking if GitHub release v$Version exists..." -ForegroundColor Cyan
    $PrevEA = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $GhCommand.Source release view "v$Version" >$null 2>&1
    $Exists = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $PrevEA

    if (-not $Exists) {
        Write-Host "Creating GitHub release v$Version..." -ForegroundColor Cyan
        & $GhCommand.Source release create "v$Version" --title "v$Version - Standalone Portable Release" --notes "Telkomsel CMP Automation Standalone Portable Release v$Version"
    }

    Write-Host "Uploading release asset $ZipPath to GitHub Release v$Version..." -ForegroundColor Cyan
    & $GhCommand.Source release upload "v$Version" $ZipPath --clobber
    if ($LASTEXITCODE -ne 0) {
        Stop-Packaging "GitHub release upload failed with exit code $LASTEXITCODE."
    }
    Write-Host "GitHub release asset uploaded successfully!" -ForegroundColor Green
}
