@echo off
set PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PYTHON_EXE%" (
    set PYTHON_EXE=python
)
cd /d "%~dp0"
"%PYTHON_EXE%" bridge.py
pause
