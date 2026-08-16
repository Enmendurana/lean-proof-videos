@echo off
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
set "PROOF_RENDERER=%~dp0.venv\Scripts\render-proof.exe"
if not exist "%PROOF_RENDERER%" (
  "%~dp0.venv\Scripts\python.exe" -m proof_video.commands.render_proof %*
  exit /b %ERRORLEVEL%
)
"%PROOF_RENDERER%" %*
