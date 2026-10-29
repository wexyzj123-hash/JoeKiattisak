@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto :noinstall
if not exist ".env" goto :noinstall
rem เปิด Hub และ Safety เบื้องหลัง (ไม่มีหน้าต่างค้าง) — เรียกด้วย "boot" (ตอนเปิดเครื่อง) จะไม่เปิดเบราว์เซอร์
if /i "%~1"=="boot" goto :boot
".venv\Scripts\python.exe" tools\service.py start --open
if errorlevel 1 goto :fail
echo เปิดแล้ว (ทำงานเบื้องหลัง) - ปิดด้วย stop.bat เปิดใหม่ด้วย RestartPortal.bat
timeout /t 4 >nul
exit /b 0

:boot
".venv\Scripts\python.exe" tools\service.py start
exit /b %errorlevel%

:fail
echo.
echo เปิดไม่สำเร็จ - ดูข้อความด้านบน และไฟล์ hub\data\hub.log / safety\data\safety.log
pause
exit /b 1

:noinstall
echo ยังไม่ได้ติดตั้ง - ให้ดับเบิลคลิก install.bat ก่อน
pause
exit /b 1
