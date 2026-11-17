@echo off
rem Start Hitokoe text call without a console window
cd /d "%~dp0"
where pythonw 1>NUL 2>NUL
if %errorlevel%==0 (
  start "" pythonw "%~dp0hitokoe_call.pyw"
) else (
  start "" python "%~dp0hitokoe_call.pyw"
)
