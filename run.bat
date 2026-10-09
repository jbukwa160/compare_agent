@echo off
rem Pre-order agent.
rem   Double-click            -> choose the supplier file, then Bulgaria only or Bulgaria + worldwide
rem   Drag a file onto this   -> same, with that file
rem   run.bat file.xlsx --scope local   -> no questions (for scripts)
rem   run.bat --check-keys    -> test the API keys in .env
setlocal
cd /d "%~dp0"

rem options only (e.g. --check-keys) or file + options: pass straight through
set "FIRST=%~1"
if "%FIRST:~0,1%"=="-" goto passthrough
if not "%~2"=="" goto passthrough

set "FILE=%~1"
if "%FILE%"=="" (
  for /f "usebackq delims=" %%F in (`powershell -NoProfile -STA -Command "Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.OpenFileDialog; $d.Title = 'Choose the supplier file'; $d.Filter = 'Supplier files (*.xlsx, *.xls, *.csv)|*.xlsx;*.xlsm;*.xls;*.csv|All files|*.*'; $d.InitialDirectory = (Resolve-Path '..').Path; if ($d.ShowDialog() -eq 'OK') { $d.FileName }"`) do set "FILE=%%F"
)
if "%FILE%"=="" (
  echo No file chosen.
  pause
  exit /b 1
)

echo.
echo Supplier file: %FILE%
echo.
echo   1 = Bulgaria only           (about 10 min for 35 products)
echo   2 = Bulgaria + worldwide    (about 20 min for 35 products)
choice /c 12 /n /m "Which check? Press 1 or 2: "
if errorlevel 2 (set "SCOPE=both") else (set "SCOPE=local")

echo.
python -m compare_agent "%FILE%" --scope %SCOPE% --open
echo.
pause
exit /b

:passthrough
python -m compare_agent %*
if errorlevel 1 pause
