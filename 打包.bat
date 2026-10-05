@echo off
cd /d "%~dp0"

echo === 检查 PyInstaller ===
python -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo 未安装，正在安装...
    python -m pip install pyinstaller || goto :fail
)

echo.
echo === 打包单文件版 dist\番茄钟.exe ===
python -m PyInstaller --noconfirm --onefile --windowed --name 番茄钟 pomodoro.py || goto :fail

echo.
echo === 打包文件夹版 dist\番茄钟-快速启动\ ===
python -m PyInstaller --noconfirm --onedir --windowed --name 番茄钟-快速启动 pomodoro.py || goto :fail

echo.
echo === 自检 ===
"dist\番茄钟.exe" --selftest
type "%TEMP%\pomodoro_tk\selftest.txt"

echo.
echo 打包完成，产物在 dist\ 目录。
pause
exit /b 0

:fail
echo.
echo 打包失败，请查看上面的错误信息。
pause
exit /b 1
