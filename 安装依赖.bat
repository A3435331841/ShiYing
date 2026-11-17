@echo off
chcp 65001 >nul
rem 拾影 · 安装运行依赖（首次使用前运行一次）
rem 实际安装逻辑与 scripts\dev\install_deps.bat 保持一致：uv 建 venv 并按 requirements.lock.txt 安装。
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  where uv >nul 2>nul
  if errorlevel 1 (
    echo 未检测到 uv，也未找到已有虚拟环境。
    echo 请先安装 uv：https://docs.astral.sh/uv/getting-started/installation/
    pause
    exit /b 1
  )
  call "%~dp0scripts\dev\install_deps.bat"
  goto :done
)
echo 已存在虚拟环境 .venv，如需重装依赖请先删除 .venv 文件夹。
echo 也可以只补装测试依赖：uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt
pause
:done
