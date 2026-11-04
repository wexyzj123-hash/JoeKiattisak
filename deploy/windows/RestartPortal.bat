@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
title AIAT - Restart Portal
echo ============================================================
echo  เปิด Hub และ Safety ใหม่ (ใช้หลังอัปเดตเวอร์ชัน หรือเมื่อระบบค้าง)
echo  ไม่ต้องติดตั้งใหม่ - ข้อมูลและ .env ไม่ถูกแตะ
echo ============================================================
if not exist ".venv\Scripts\python.exe" goto :noinstall
if not exist ".env" goto :noinstall
rem ปิดหน้าต่างแบบเก่าที่อาจค้างอยู่ (ถ้าเคยเปิดด้วย start.bat เวอร์ชันก่อน) แล้วค่อยเปิดใหม่เบื้องหลัง
taskkill /FI "WINDOWTITLE eq AIAT-Hub*" /T /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq AIAT-Safety*" /T /F >nul 2>nul
".venv\Scripts\python.exe" tools\service.py restart
if errorlevel 1 goto :fail
echo.
echo เสร็จแล้ว - ระบบทำงานเบื้องหลัง ผู้ใช้เปิดหน้าเว็บเดิมได้เลย
timeout /t 5 >nul
exit /b 0

:fail
echo.
echo [X] เปิดใหม่ไม่สำเร็จ - ดูข้อความด้านบน และไฟล์ hub\data\hub.log / safety\data\safety.log
pause
exit /b 1

:noinstall
echo ยังไม่ได้ติดตั้ง - ให้ดับเบิลคลิก install.bat ก่อน (ครั้งแรกครั้งเดียว)
pause
exit /b 1
