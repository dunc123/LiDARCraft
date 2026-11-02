@echo off
setlocal EnableDelayedExpansion
title LiDARCraft - 1:1 Real-World Minecraft Builder & Server
cd /d "%~dp0"

echo ============================================================
echo   LiDARCraft - 1:1 Real-World LiDAR ^& OpenStreetMap Server
echo ============================================================
echo.

:: 1. Locate or auto-download portable Python 3.12 (no manual install required)
set "PY_BIN="
python --version >nul 2>nul
if !errorlevel! equ 0 (
    set "PY_BIN=python"
) else if exist "%~dp0.runtime\python\python.exe" (
    set "PY_BIN=%~dp0.runtime\python\python.exe"
) else (
    echo [*] Python not found on PC - downloading portable Python 3.12 into .runtime\python ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "$ErrorActionPreference='Stop'; New-Item -ItemType Directory -Force -Path '.runtime\python' | Out-Null; " ^
        "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip' -OutFile '.runtime\py.zip'; " ^
        "Expand-Archive -Path '.runtime\py.zip' -DestinationPath '.runtime\python' -Force; " ^
        "$pth = Get-Content '.runtime\python\python312._pth'; $pth = $pth -replace '#import site','import site'; Set-Content '.runtime\python\python312._pth' $pth; " ^
        "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '.runtime\get-pip.py'; " ^
        "& '.runtime\python\python.exe' '.runtime\get-pip.py' --no-warn-script-location | Out-Null"
    set "PY_BIN=%~dp0.runtime\python\python.exe"
)

:: 2. Locate Java 21+ (System PATH, Program Files, Minecraft Launcher runtime, or auto-download portable JRE 21)
set "JAVA_BIN="
for /f "delims=" %%J in ('powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$candidates = @('java'); " ^
    "$candidates += Get-ChildItem 'C:\Program Files\Java\*\bin\java.exe','C:\Program Files\Eclipse Adoptium\*\bin\java.exe' -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -ExpandProperty FullName; " ^
    "$candidates += Get-ChildItem \"$env:LOCALAPPDATA\Packages\Microsoft.4297127D64EC6_8wekyb3d8bbwe\LocalCache\Local\runtime\java-runtime-delta\windows-x64\java-runtime-delta\bin\java.exe\",\"${env:ProgramFiles(x86)}\Minecraft Launcher\runtime\java-runtime-delta\windows-x64\java-runtime-delta\bin\java.exe\" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName; " ^
    "if (Test-Path '.runtime\java\bin\java.exe') { $candidates += (Resolve-Path '.runtime\java\bin\java.exe').Path }; " ^
    "foreach ($c in $candidates) { try { $v = & $c -version 2>&1 | Out-String; if ($v -match 'version \"(2[1-9]|[3-9]\d)') { Write-Output $c; break } } catch {} }"') do (
    set "JAVA_BIN=%%J"
)

if "!JAVA_BIN!"=="" (
    echo [*] Java 21+ not found on PC - downloading portable OpenJDK 21 into .runtime\java ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "$ErrorActionPreference='Stop'; New-Item -ItemType Directory -Force -Path '.runtime\java_tmp' | Out-Null; " ^
        "Invoke-WebRequest -Uri 'https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jre/hotspot/normal/eclipse' -OutFile '.runtime\jre21.zip'; " ^
        "Expand-Archive -Path '.runtime\jre21.zip' -DestinationPath '.runtime\java_tmp' -Force; " ^
        "$inner = Get-ChildItem '.runtime\java_tmp' | Select-Object -First 1; Move-Item $inner.FullName '.runtime\java' -Force"
    set "JAVA_BIN=%~dp0.runtime\java\bin\java.exe"
)

echo [1/3] Checking Python dependencies...
"!PY_BIN!" -m pip install -q -r "%~dp0requirements.txt"

echo.
set "LOC=%~1"
if "!LOC!"=="" (
    set /p "LOC=Enter a place name, postcode, or Google Maps URL (default: Glastonbury): "
)
if "!LOC!"=="" set "LOC=Glastonbury"

echo.
echo [2/3] Building 1:1 map for "!LOC!" and starting Minecraft server...
"!PY_BIN!" "%~dp0build_map.py" "!LOC!" --spawn --run
pause
