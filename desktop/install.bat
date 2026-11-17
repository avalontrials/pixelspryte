@echo off
setlocal EnableDelayedExpansion
title PixelSpryte Installer
echo.
echo  =============================================
echo   PixelSpryte - AI Edit Pipeline for Resolve
echo  =============================================
echo.
cd /d "%~dp0"

rem --- 1. Check Python ---------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo  ERROR: Python not found on PATH.
    echo  Install Python 3.10+ from https://python.org (check "Add to PATH"^)
    pause & exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
    echo  ERROR: Python 3.10 or newer is required.
    pause & exit /b 1
)
echo  [1/5] Python OK

rem --- 2. Install packages ------------------------------------------
echo  [2/5] Installing Python packages (this can take a few minutes)...
python -m pip install --quiet -r requirements.txt ruamel.yaml pillow
if errorlevel 1 (
    echo  ERROR: package installation failed. Check your internet connection.
    pause & exit /b 1
)

rem --- 3. API key ----------------------------------------------------
if exist .env (
    echo  [3/5] .env already present - keeping existing API key
    goto :shortcut
)
echo.
set "APIKEY="
set /p APIKEY="  Enter your DeepSeek API key (Enter to skip, add later in Settings): "
if defined APIKEY (
    echo DEEPSEEK_API_KEY=!APIKEY!> .env
    echo  [3/5] API key saved to .env
) else (
    copy .env.example .env >nul 2>nul
    echo  [3/5] Skipped - open Settings in the app to add your key
)

:shortcut

rem --- 4. Desktop shortcut -------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$s = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\PixelSpryte.lnk');" ^
  "$py = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source;" ^
  "if (-not $py) { $py = (Get-Command python.exe).Source };" ^
  "$s.TargetPath = $py; $s.Arguments = '\"%~dp0ui.py\"';" ^
  "$s.WorkingDirectory = '%~dp0';" ^
  "if (Test-Path '%~dp0assets\pixelspryte.ico') { $s.IconLocation = '%~dp0assets\pixelspryte.ico' };" ^
  "$s.Save()"
echo  [4/5] Desktop shortcut created

rem --- 5. DaVinci Resolve Scripts menu integration -------------------
set "RDIR=%APPDATA%\Blackmagic Design\DaVinci Resolve"
set "RSCRIPTS=%RDIR%\Support\Fusion\Scripts\Utility"
if exist "%RDIR%" (
    if not exist "!RSCRIPTS!" mkdir "!RSCRIPTS!" 2>nul
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "(Get-Content '%~dp0resolve_menu_launcher.py') -replace '__APP_DIR__', '%~dp0'.TrimEnd('\') | Set-Content '!RSCRIPTS!\PixelSpryte.py'"
    echo  [5/5] Added to Resolve: Workspace ^> Scripts ^> PixelSpryte
) else (
    echo  [5/5] DaVinci Resolve not detected - skipped Scripts menu entry
)

echo.
echo  Install complete!
echo  Launch: desktop shortcut, or inside Resolve via Workspace ^> Scripts ^> PixelSpryte
echo.
pause
