@echo off
REM install_microcore.bat - Install all dependencies for MicroCore on Windows

echo ==========================================
echo MICROCORE (MCX) DEPENDENCY INSTALLER
echo ==========================================
echo.

REM Check Python
echo Checking Python version...
python --version
if %errorlevel% neq 0 (
    echo ERROR: Python not found. Please install Python 3.7+ from python.org
    pause
    exit /b 1
)

REM Upgrade pip
echo.
echo Upgrading pip...
python -m pip install --upgrade pip

REM Install core dependencies
echo.
echo Installing core dependencies...

echo   - websockets
pip install websockets

echo   - cryptography
pip install cryptography

echo   - requests
pip install requests

echo   - dnspython
pip install dnspython

echo   - pyserial
pip install pyserial

echo   - colorama
pip install colorama

echo   - ujson
pip install ujson 2>nul

echo.
echo ==========================================
echo INSTALLATION COMPLETE!
echo ==========================================
echo.
echo Installed packages:
pip list | findstr /i "websockets cryptography requests dnspython pyserial colorama"

echo.
echo To verify installation, run:
echo   python -c "import websockets, cryptography, requests; print('OK')"
echo.
echo To start a node:
echo   python node_full.py --genesis --username YOUR_NAME
echo.
echo To start a miner:
echo   python pc_miner.py
echo.
pause
