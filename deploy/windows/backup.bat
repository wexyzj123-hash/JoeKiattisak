@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmm"') do set "STAMP=%%i"
set "DEST=backup\%STAMP%"
mkdir "%DEST%" >nul 2>nul
rem ใช้ sqlite backup API (ปลอดภัยขณะระบบเปิดอยู่) แล้วคัดลอกรูปภาพ
".venv\Scripts\python.exe" tools\backup.py "%DEST%" || (echo [X] สำรองไม่สำเร็จ & pause & exit /b 1)
echo สำรองไว้ที่ %CD%\%DEST%  — คัดลอกโฟลเดอร์ backup ไปเก็บนอกเครื่องเป็นประจำ
pause
