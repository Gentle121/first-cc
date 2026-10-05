@echo off
rem 双击本文件即可启动番茄钟（优先用 pythonw，不弹出黑色控制台窗口）
cd /d "%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 (
    python pomodoro.py
) else (
    start "" pythonw pomodoro.py
)
