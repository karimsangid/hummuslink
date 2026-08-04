@echo off
:: Creates a Windows startup shortcut so HummusLink runs on boot (hidden, no console window)
echo Creating HummusLink startup entry...

set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set VBS=%STARTUP%\hummuslink.vbs

:: Create a VBS wrapper that runs HummusLink without showing a console window
echo Set WshShell = CreateObject("WScript.Shell") > "%VBS%"
echo WshShell.CurrentDirectory = "C:\Users\reach\Projects\hummuslink" >> "%VBS%"
echo WshShell.Run "python main.py", 0, False >> "%VBS%"

echo.
echo Done! HummusLink will now start automatically when you log in.
echo The server runs silently in the background with a system tray icon.
echo.
echo To remove: delete %VBS%
pause
