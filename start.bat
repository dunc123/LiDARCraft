<# :
@echo off
cd /d "%~dp0"
title LiDARCraft - 1:1 Real-World Minecraft Builder
powershell -NoProfile -ExecutionPolicy Bypass -Command "$rootDir = (Get-Location).Path; Invoke-Expression ([System.IO.File]::ReadAllText('%~f0'))"
echo.
pause
exit /b
#>

$ErrorActionPreference = "Stop"
if (-not $rootDir) { $rootDir = (Get-Location).Path }
Set-Location $rootDir

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  LiDARCraft - 1:1 Real-World LiDAR & OpenStreetMap Server" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

try {
    # 1. Locate or bootstrap Python 3.12 (with pip)
    $pyBin = $null
    if (-not $env:LIDARCRAFT_FORCE_PORTABLE) {
        try {
            $ErrorActionPreference = "Continue"
            $verOut = & python --version 2>&1 | Out-String
            if ($LASTEXITCODE -eq 0 -and $verOut -match "Python 3\.") {
                & python -m pip --version 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    $pyBin = "python"
                }
            }
            $ErrorActionPreference = "Stop"
        } catch {
            $ErrorActionPreference = "Stop"
        }
    }

    $localPy = Join-Path $rootDir ".runtime\python\python.exe"
    $localPip = Join-Path $rootDir ".runtime\python\Scripts\pip.exe"

    if (-not $pyBin) {
        if (-not (Test-Path $localPy) -or -not (Test-Path $localPip)) {
            Write-Host "[1/3] Downloading portable Python 3.12 into .runtime\python ..." -ForegroundColor Yellow
            New-Item -ItemType Directory -Force -Path ".runtime\python" | Out-Null
            $pyZip = ".runtime\py.zip"
            Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip" -OutFile $pyZip -UseBasicParsing
            Expand-Archive -Path $pyZip -DestinationPath ".runtime\python" -Force

            $pthFile = ".runtime\python\python312._pth"
            if (Test-Path $pthFile) {
                $pth = Get-Content $pthFile
                $pth = $pth -replace "#import site", "import site"
                Set-Content -Path $pthFile -Value $pth -Encoding ASCII
            }

            Write-Host "      Installing pip for portable Python..." -ForegroundColor Yellow
            $getPip = ".runtime\get-pip.py"
            Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing
            $ErrorActionPreference = "Continue"
            & $localPy $getPip --no-warn-script-location
            $ErrorActionPreference = "Stop"
        }
        $pyBin = $localPy
    }

    # 2. Locate or bootstrap Java 21+ (System PATH, Program Files, Minecraft Launcher runtime, or portable Adoptium JRE 21)
    $javaBin = $null
    $candidates = @("java")
    $candidates += Get-ChildItem "C:\Program Files\Java\*\bin\java.exe", "C:\Program Files\Eclipse Adoptium\*\bin\java.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -ExpandProperty FullName
    $mcJava1 = Join-Path $env:LOCALAPPDATA "Packages\Microsoft.4297127D64EC6_8wekyb3d8bbwe\LocalCache\Local\runtime\java-runtime-delta\windows-x64\java-runtime-delta\bin\java.exe"
    $mcJava2 = Join-Path ${env:ProgramFiles(x86)} "Minecraft Launcher\runtime\java-runtime-delta\windows-x64\java-runtime-delta\bin\java.exe"
    if (Test-Path $mcJava1) { $candidates += $mcJava1 }
    if (Test-Path $mcJava2) { $candidates += $mcJava2 }
    $localJava = Join-Path $rootDir ".runtime\java\bin\java.exe"
    if (Test-Path $localJava) { $candidates += $localJava }

    foreach ($c in $candidates) {
        try {
            $ErrorActionPreference = "Continue"
            $vOut = & $c -version 2>&1 | Out-String
            $ErrorActionPreference = "Stop"
            if ($vOut -match 'version "(2[1-9]|[3-9]\d)') {
                $javaBin = $c
                break
            }
        } catch {
            $ErrorActionPreference = "Stop"
        }
    }

    if (-not $javaBin) {
        Write-Host "[*] Downloading portable OpenJDK 21 into .runtime\java ..." -ForegroundColor Yellow
        if (Test-Path ".runtime\java_tmp") { Remove-Item -Recurse -Force ".runtime\java_tmp" }
        if (Test-Path ".runtime\java") { Remove-Item -Recurse -Force ".runtime\java" }
        New-Item -ItemType Directory -Force -Path ".runtime\java_tmp" | Out-Null
        $jreZip = ".runtime\jre21.zip"
        Invoke-WebRequest -Uri "https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jre/hotspot/normal/eclipse" -OutFile $jreZip -UseBasicParsing
        Expand-Archive -Path $jreZip -DestinationPath ".runtime\java_tmp" -Force
        $inner = Get-ChildItem ".runtime\java_tmp" | Select-Object -First 1
        Move-Item $inner.FullName ".runtime\java" -Force
        Remove-Item -Recurse -Force ".runtime\java_tmp" -ErrorAction SilentlyContinue
        $javaBin = $localJava
    }

    $env:JAVA_BIN = $javaBin
    Write-Host "Using Python : $pyBin" -ForegroundColor DarkGray
    Write-Host "Using Java   : $javaBin" -ForegroundColor DarkGray

    # 3. Install Python packages
    Write-Host "[2/3] Checking Python packages (numpy, Pillow, requests, pyproj)..." -ForegroundColor Gray
    $ErrorActionPreference = "Continue"
    & $pyBin -m pip install -q --no-warn-script-location -r (Join-Path $rootDir "requirements.txt")
    $pipExit = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($pipExit -ne 0) {
        throw "Failed to install Python packages from requirements.txt (exit code $pipExit)"
    }

    # 4. Prompt for location and launch build_map.py
    Write-Host ""
    $loc = $env:LIDARCRAFT_TEST_LOC
    if ([string]::IsNullOrWhiteSpace($loc)) {
        $loc = Read-Host "Enter a place name, postcode, or Google Maps URL (press Enter for Glastonbury)"
    }
    if ([string]::IsNullOrWhiteSpace($loc)) {
        $loc = "Glastonbury"
    }

    Write-Host ""
    Write-Host "[3/3] Building 1:1 map for '$loc'..." -ForegroundColor Green
    $ErrorActionPreference = "Continue"
    if ($env:LIDARCRAFT_NO_RUN) {
        & $pyBin (Join-Path $rootDir "build_map.py") $loc --spawn
    } else {
        & $pyBin (Join-Path $rootDir "build_map.py") $loc --spawn --run
    }
    $ErrorActionPreference = "Stop"
} catch {
    Write-Host ""
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkRed
}
