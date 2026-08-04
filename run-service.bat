@echo off
cd /d "%~dp0"
set LOGDIR=%USERPROFILE%\.hummuslink
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set LOG=%LOGDIR%\hummuslink.log
echo. >> "%LOG%"
echo ---- %DATE% %TIME% starting HummusLink ---- >> "%LOG%"
"C:\Users\reach\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe" main.py >> "%LOG%" 2>&1
echo ---- %DATE% %TIME% exited with code %ERRORLEVEL% ---- >> "%LOG%"
