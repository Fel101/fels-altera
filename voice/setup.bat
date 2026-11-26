@echo off
rem Install Altera's local voice service: its own environment in voice\.venv, then Kokoro's model and voices.
rem Kokoro needs Python 3.10, 3.11 or 3.12; this uses the Python launcher's 3.12 unless PYTHON is set.
setlocal
set "HERE=%~dp0"
if "%PYTHON%"=="" set "PYTHON=py -3.12"
if not defined ALTERA_VOICE_ENV set "ALTERA_VOICE_ENV=%HERE%.venv"
if not exist "%ALTERA_VOICE_ENV%\Scripts\python.exe" (
    echo Making "%ALTERA_VOICE_ENV%"
    if defined ALTERA_VOICE_BOOTSTRAP (
        "%ALTERA_VOICE_BOOTSTRAP%" -m venv "%ALTERA_VOICE_ENV%" || goto failed
    ) else (
        %PYTHON% -m venv "%ALTERA_VOICE_ENV%" || goto failed
    )
)
"%ALTERA_VOICE_ENV%\Scripts\python.exe" -m pip install --upgrade pip || goto failed
"%ALTERA_VOICE_ENV%\Scripts\python.exe" -m pip install -r "%HERE%requirements.txt" || goto failed
"%ALTERA_VOICE_ENV%\Scripts\python.exe" "%HERE%serve.py" --download || goto failed
echo.
echo The voice service is installed. In Altera's Settings, switch Voice on.
exit /b 0
:failed
echo.
echo The voice service was not installed. The message above says why.
exit /b 1
