@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -m proof_video.studio.launcher start --root "%CD%" %*
