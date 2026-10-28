@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
net session >nul 2>nul || (echo ต้องคลิกขวาไฟล์นี้ แล้วเลือก "Run as administrator" & pause & exit /b 1)
echo ตั้งให้เปิดระบบอัตโนมัติเมื่อเปิดเครื่อง + เปิดไฟร์วอลล์พอร์ต 5000 และ 5200 ...
schtasks /Create /TN "AIAT Safety" /TR "\"%~dp0start.bat\"" /SC ONSTART /RL HIGHEST /F
netsh advfirewall firewall delete rule name="AIAT Safety Hub" >nul 2>nul
netsh advfirewall firewall add rule name="AIAT Safety Hub" dir=in action=allow protocol=TCP localport=5000,5200
echo เสร็จแล้ว (ยกเลิก: schtasks /Delete /TN "AIAT Safety" /F)
pause
