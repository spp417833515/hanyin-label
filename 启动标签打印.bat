@echo off
rem 启动汉印标签打印服务并打开程序窗口 (服务已在运行时只打开窗口)
cd /d "%~dp0"
rem 清掉可能残留的时区变量, 否则日期时间会算错
set TZ=
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw server.py --open
) else (
    start "" python server.py --open
)
