@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
echo กำลังปิด Hub และ Safety ...
if exist ".venv\Scripts\python.exe" ".venv\Scripts\python.exe" tools\service.py stop
rem เวอร์ชันเก่าเปิดเป็นหน้าต่างย่อ (ตั้งชื่อหน้าต่างไว้) — ปิดให้ด้วยถ้ายังค้างอยู่
taskkill /FI "WINDOWTITLE eq AIAT-Hub*" /T /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq AIAT-Safety*" /T /F >nul 2>nul
echo ปิดแล้ว
timeout /t 3 >nul
