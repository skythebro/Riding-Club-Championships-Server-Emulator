@echo off
rem Run the server from source. RCCServer.exe from a release does not need this.
rem Optional: start_server.bat "D:\Games\RidingClubChampionships"   (the folder with rcc.exe)
cd /d "%~dp0"

if not exist ".venv" (
    echo Creating Python virtual environment...
    python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt

if not exist "igor_host\IgorHost.exe" (
    echo Building the card host...
    pushd igor_host
    C:\WINDOWS\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo /target:exe /main:Host /r:System.Web.Extensions.dll /out:IgorHost.exe Host.cs
    popd
)

rem The server starts the card host itself and stops it on exit.
if "%~1"=="" (
    python Server.py
) else (
    python Server.py --game-dir "%~1"
)
pause
