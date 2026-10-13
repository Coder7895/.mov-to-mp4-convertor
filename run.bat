@echo off
setlocal
cd /d "%~dp0"

:: Ensure MSYS2 ucrt64 tools (Python + FFmpeg) are at the front of PATH
set "PATH=C:\msys64\ucrt64\bin;%PATH%"

echo ========================================================
echo   MOV to MP4 Converter - Desktop Application
echo ========================================================
echo.

if exist "C:\msys64\ucrt64\bin\python.exe" (
    "C:\msys64\ucrt64\bin\python.exe" app.py %*
) else (
    python app.py %*
)

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] An error occurred while running the application.
    pause
)
