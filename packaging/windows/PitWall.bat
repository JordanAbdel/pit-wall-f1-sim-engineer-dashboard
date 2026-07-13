@echo off
REM Double-click to run PitWall. A console window opens and starts the app.
REM Keep this window open while you're driving -- closing it stops the app.
cd /d "%~dp0"
echo Starting PitWall...
echo (Keep this window open while you're driving - closing it stops the app.)
echo.
"%~dp0PitWall\PitWall.exe"
echo.
echo PitWall has stopped. You can close this window.
pause
