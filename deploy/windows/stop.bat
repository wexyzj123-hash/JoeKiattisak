@echo off
chcp 65001 >nul
echo กำลังปิด Hub และ Safety ...
taskkill /FI "WINDOWTITLE eq AIAT-Hub*" /T /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq AIAT-Safety*" /T /F >nul 2>nul
echo ปิดแล้ว
timeout /t 3 >nul
