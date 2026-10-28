@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto :noinstall
if not exist ".env" goto :noinstall

for /f "tokens=1,2" %%a in ('".venv\Scripts\python.exe" tools\ports.py show') do (set "HUB_PORT=%%a" & set "SAFETY_PORT=%%b")
set "BUSY="
for /f "delims=" %%p in ('".venv\Scripts\python.exe" tools\ports.py inuse') do set "BUSY=%%p"
if defined BUSY goto :busy

echo กำลังเปิด Hub (พอร์ต %HUB_PORT%) และ Safety (พอร์ต %SAFETY_PORT%) ...
start "AIAT-Hub" /min cmd /c ".venv\Scripts\python.exe hub\run.py >> hub\data\hub.log 2>&1"
start "AIAT-Safety" /min cmd /c ".venv\Scripts\python.exe safety\run.py >> safety\data\safety.log 2>&1"
timeout /t 4 >nul
start "" "http://localhost:%HUB_PORT%"
echo เปิดแล้ว - ปิดระบบด้วย stop.bat
timeout /t 5 >nul
exit /b 0

:noinstall
echo ยังไม่ได้ติดตั้ง - ให้ดับเบิลคลิก install.bat ก่อน
pause
exit /b 1

:busy
echo [X] พอร์ตนี้มีโปรแกรมอื่นใช้อยู่แล้ว: %BUSY%
echo     ถ้าเป็นระบบนี้ที่เปิดค้างอยู่ ให้รัน stop.bat ก่อน
echo     ถ้าเป็นระบบอื่น (เช่น E-Checksheet / UTCMMS) ให้แก้ HUB_PORT / SAFETY_PORT / HUB_URL / SAFETY_URL ใน .env เป็นพอร์ตอื่น แล้วรัน start.bat ใหม่
pause
exit /b 1
