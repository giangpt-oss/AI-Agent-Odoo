@echo off
chcp 65001 > nul
title Kiểm tra kết nối Odoo ERP
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Không tìm thấy môi trường ảo .venv!
    pause
    exit /b 1
)

".venv\Scripts\python.exe" scripts/test_odoo_connection.py

echo.
pause
