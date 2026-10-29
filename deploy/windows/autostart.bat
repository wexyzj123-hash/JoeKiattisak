@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
net session >nul 2>nul || goto :notadmin
if not exist ".venv\Scripts\python.exe" goto :noinstall
for /f "tokens=1,2" %%a in ('".venv\Scripts\python.exe" tools\ports.py show') do (set "HUB_PORT=%%a" & set "SAFETY_PORT=%%b")
echo ตั้งให้เปิดระบบอัตโนมัติเมื่อเปิดเครื่อง + เปิดไฟร์วอลล์พอร์ต %HUB_PORT% และ %SAFETY_PORT% ...
schtasks /Create /TN "AIAT Safety" /TR "\"%~dp0start.bat\" boot" /SC ONSTART /RL HIGHEST /F
netsh advfirewall firewall delete rule name="AIAT Safety Hub" >nul 2>nul
netsh advfirewall firewall add rule name="AIAT Safety Hub" dir=in action=allow protocol=TCP localport=%HUB_PORT%,%SAFETY_PORT%
echo เสร็จแล้ว - ยกเลิก: schtasks /Delete /TN "AIAT Safety" /F
pause
exit /b 0

:notadmin
echo ต้องคลิกขวาไฟล์นี้ แล้วเลือก Run as administrator
pause
exit /b 1

:noinstall
echo ยังไม่ได้ติดตั้ง - ให้ดับเบิลคลิก install.bat ก่อน
pause
exit /b 1
