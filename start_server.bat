@echo off
echo Starting Riding Club Championships Server Emulator...
echo.

REM Navigate to the server directory
cd /d "%~dp0"

REM Create images directory if it doesn't exist
if not exist "images" mkdir images

REM Check if Python virtual environment exists
if not exist ".venv" (
    echo Creating Python virtual environment...
    python -m venv .venv
)

REM Activate virtual environment and run server
echo Activating virtual environment...
call .venv\Scripts\activate.bat

REM Install dependencies if needed
echo Installing/updating dependencies...
pip install fastapi uvicorn websockets aiofiles python-multipart UnityPy

echo.
echo Starting server...
echo The server will be available at:
echo   HTTP: http://127.0.0.1:80
echo   WebSocket: ws://127.0.0.1:80/websocket
echo.
echo Press Ctrl+C to stop the server
echo.

python Server.py

pause
