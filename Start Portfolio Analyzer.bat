@echo off
title Portfolio Analyzer
cd /d "%~dp0"
echo Starting Portfolio Analyzer... your browser will open at http://localhost:8501
echo Close this window (or press Ctrl+C) to stop the app.
echo.
if exist ".venv\Scripts\python.exe" goto run
if exist ".tools\uv.exe" (
    ".tools\uv.exe" sync --locked
) else (
    where uv >nul 2>nul
    if errorlevel 1 (
        echo UV is missing. Install UV, then run this launcher again.
        pause
        exit /b 1
    )
    uv sync --locked
)
if errorlevel 1 (
    echo Setup failed. Review the error above.
    pause
    exit /b 1
)
:run
".venv\Scripts\python.exe" -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
echo.
echo The app has stopped. If you see an error above, copy it and share it.
pause
