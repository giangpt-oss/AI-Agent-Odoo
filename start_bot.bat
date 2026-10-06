@echo off
chcp 65001 > nul
title Hopita AI Agent - Telegram Bot
cd /d "%~dp0"

echo ============================================================
echo   🚀 KHỞI ĐỘNG HOPITA AI AGENT (TELEGRAM BOT)
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Không tìm thấy môi trường ảo .venv!
    pause
    exit /b 1
)

".venv\Scripts\python.exe" scripts/run_telegram_bot_polling.py

echo.
echo [INFO] Bot đã dừng. Nhấn phím bất kỳ để thoát...
pause
