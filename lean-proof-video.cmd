@echo off
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
"%~dp0.venv\Scripts\python.exe" -m proof_video.cli %*
exit /b %ERRORLEVEL%
