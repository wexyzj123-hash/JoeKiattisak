@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo ยังไม่ได้ติดตั้ง — ให้ดับเบิลคลิก install.bat ก่อน
  pause
  exit /b 1
)
if not exist ".env" (
  echo ไม่พบไฟล์ .env — ให้รัน install.bat ก่อน
  pause
  exit /b 1
)
echo กำลังเปิด Hub (พอร์ต 5000) และ Safety (พอร์ต 5200) ...
start "AIAT-Hub" /min cmd /c ".venv\Scripts\python.exe hub\run.py >> hub\data\hub.log 2>&1"
start "AIAT-Safety" /min cmd /c ".venv\Scripts\python.exe safety\run.py >> safety\data\safety.log 2>&1"
timeout /t 4 >nul
start "" "http://localhost:5000"
echo เปิดแล้ว — ปิดระบบด้วย stop.bat (ห้ามปิดหน้าต่างที่ย่ออยู่ถ้ายังต้องใช้งาน)
timeout /t 5 >nul
exit /b 0
