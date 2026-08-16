@echo off
setlocal
set "PROJECT_ROOT=%~dp0"
set "PYTHONPATH=%PROJECT_ROOT%;%PYTHONPATH%"
"%PROJECT_ROOT%.venv\Scripts\python.exe" -m proof_video.commands.cache %*
exit /b %ERRORLEVEL%
