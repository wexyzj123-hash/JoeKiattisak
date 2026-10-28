@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title AIAT Safety - Install
echo ============================================================
echo  AIAT Safety + Hub  -  ติดตั้ง (Install)
echo ============================================================

rem ---- 1) หา Python 3.10+ ----
set "PY="
where py >nul 2>nul && py -3 -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=py -3"
if defined PY goto :py_ok
where python >nul 2>nul && python -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=python"
if defined PY goto :py_ok
echo [!] ไม่พบ Python 3.10 ขึ้นไป - กำลังลองติดตั้งด้วย winget ...
where winget >nul 2>nul && winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
echo.
echo ถ้าติดตั้ง Python เสร็จแล้ว ให้ปิดหน้าต่างนี้ แล้วดับเบิลคลิก install.bat ใหม่อีกครั้ง
echo ถ้ายังไม่มี Python: โหลดที่ https://www.python.org/downloads/ โดยติ๊ก Add python.exe to PATH
pause
exit /b 1

:py_ok
echo [OK] ใช้ Python: %PY%

rem ---- 2) สร้าง virtual environment และติดตั้งไลบรารี ----
if exist ".venv\Scripts\python.exe" goto :venv_ok
echo กำลังสร้าง .venv ...
%PY% -m venv .venv || goto :fail
:venv_ok
echo กำลังติดตั้งไลบรารี - ต้องต่ออินเทอร์เน็ต ...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail

rem ---- 3) สร้าง .env (ความลับสุ่ม + ที่อยู่ + พอร์ต) ----
if exist ".env" goto :env_done
echo.
echo ที่อยู่ที่ผู้ใช้จะเปิดเว็บ: ถ้าใช้เครื่องนี้เครื่องเดียวกด Enter
echo ถ้าเปิดจากเครื่องอื่นในบริษัท ให้พิมพ์ชื่อเครื่อง หรือ IP ของเซิร์ฟเวอร์นี้ เช่น 192.168.1.10
set "HOSTNAME_IN="
set /p HOSTNAME_IN=ที่อยู่เซิร์ฟเวอร์ [localhost]: 
echo.
echo ตรวจหาพอร์ตว่าง (ไม่ชนกับระบบอื่นบนเครื่อง เช่น E-Checksheet / UTCMMS) ...
for /f %%p in ('".venv\Scripts\python.exe" tools\ports.py suggest 5000') do set "SUG_H=%%p"
set "HUBP="
set /p HUBP=พอร์ตของ Hub [%SUG_H%]: 
if "%HUBP%"=="" set "HUBP=%SUG_H%"
for /f %%p in ('".venv\Scripts\python.exe" tools\ports.py suggest 5200 %HUBP%') do set "SUG_S=%%p"
set "SAFP="
set /p SAFP=พอร์ตของ Safety [%SUG_S%]: 
if "%SAFP%"=="" set "SAFP=%SUG_S%"
".venv\Scripts\python.exe" tools\make_env.py "%HOSTNAME_IN%" %HUBP% %SAFP% || goto :fail
:env_done

rem ---- 4) โฟลเดอร์ข้อมูลและสำรอง ----
if not exist "hub\data" mkdir "hub\data"
if not exist "safety\data" mkdir "safety\data"
if not exist "backup" mkdir "backup"

rem ---- 5) ตรวจว่าเปิดระบบได้ (โหลดแอปทั้งสองตัว) ----
".venv\Scripts\python.exe" tools\selfcheck.py || goto :fail

for /f "tokens=1,2" %%a in ('".venv\Scripts\python.exe" tools\ports.py show') do (set "HUB_PORT=%%a" & set "SAFETY_PORT=%%b")
echo.
echo ============================================================
echo  ติดตั้งเสร็จแล้ว  (Hub พอร์ต %HUB_PORT% / Safety พอร์ต %SAFETY_PORT%)
echo   1) ดับเบิลคลิก start.bat เพื่อเปิดระบบ
echo   2) เปิดเว็บที่ Hub แล้วสร้างผู้ดูแลระบบคนแรกที่หน้า /setup
echo   3) ทำตามรายการตรวจสอบใน docs\DEPLOY.md
echo  ให้เปิดระบบอัตโนมัติเมื่อเปิดเครื่อง: คลิกขวา autostart.bat แล้วเลือก Run as administrator
echo  เปลี่ยนพอร์ตภายหลัง: แก้ HUB_PORT / SAFETY_PORT / HUB_URL / SAFETY_URL ใน .env
echo ============================================================
pause
exit /b 0

:fail
echo.
echo [X] ติดตั้งไม่สำเร็จ - ดูข้อความด้านบน แล้วลองใหม่ (ตรวจอินเทอร์เน็ต/สิทธิ์โฟลเดอร์)
pause
exit /b 1
