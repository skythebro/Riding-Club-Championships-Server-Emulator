@echo off
echo Starting Riding Club Championships Server Emulator...
echo.

cd /d "%~dp0"
if not exist "images" mkdir images

if not exist ".venv" (
    echo Creating Python virtual environment...
    python -m venv .venv
)

call .venv\Scripts\activate.bat
pip install -q -r requirements.txt

set "MANAGED=%~dp0..\rcc_Data\Managed"
if not "%~1"=="" set "MANAGED=%~1"

if not exist "igor_host\IgorHost.exe" (
    echo Building the card host...
    pushd igor_host
    C:\WINDOWS\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo /target:exe /main:Host /r:System.Web.Extensions.dll /out:IgorHost.exe Host.cs
    popd
)

if exist "igor_host\IgorHost.exe" (
    echo Starting the card host...
    start "RCC card host" "%~dp0igor_host\IgorHost.exe" "%MANAGED%" 27141
) else (
    echo The card host did not build. The shop and cards will not load until IgorHost.exe is running.
)

echo.
echo Starting server...
echo   HTTP: http://127.0.0.1:8080 (or http_port in server.json)
echo   Game: 127.0.0.1:27130
echo.
echo Press Ctrl+C to stop the server. Close the card host window too.
echo.

python Server.py
pause
