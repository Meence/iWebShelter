@echo off
chcp 65001

:: 设置便携版Python路径
set "PYTHON_DIR=iPrograms"
set "PYTHON_EXE=python.exe"
set "APP_SCRIPT=app.py"

:: 切换到项目根目录
cd /d "%~dp0"

:: 输出信息
cls
echo 正在使用便携版 Python 启动项目...

:: 清理现有的PYTHONPATH，避免冲突
set "PYTHONPATH="

:: 确保项目根目录被添加到PYTHONPATH
set "PYTHONPATH=%CD%"

:: 确保iPrograms中的site-packages也被添加到PYTHONPATH
set "PYTHONPATH=%PYTHONPATH%;%CD%\%PYTHON_DIR%\Lib\site-packages"

:: 检查Python可执行文件是否存在
if exist "%CD%\%PYTHON_DIR%\%PYTHON_EXE%" (
    "%CD%\%PYTHON_DIR%\%PYTHON_EXE%" "%APP_SCRIPT%"
    echo Python执行退出码: %ERRORLEVEL%
) else (
    echo Error: 找不到Python可执行文件 "%CD%\%PYTHON_DIR%\%PYTHON_EXE%"
    pause
    exit /b 1
)

echo 项目已结束
pause