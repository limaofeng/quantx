@echo off
REM 使用 pytest 运行测试

echo ========================================
if defined QUANTX_CONDA_ENV (
    echo 使用 pytest + conda 环境: %QUANTX_CONDA_ENV%
) else (
    echo 使用 pytest + 当前 Python 环境
)
echo ========================================
echo.

REM 可选激活 conda 环境
if defined QUANTX_CONDA_ENV (
    call conda activate %QUANTX_CONDA_ENV%
    if errorlevel 1 (
        echo 错误: 无法激活 conda 环境 %QUANTX_CONDA_ENV%
        echo 请确保已安装 conda 并创建了该环境
        pause
        exit /b 1
    )
)

echo 当前 Python 环境:
python --version
python -c "import sys; print(f'Python 路径: {sys.executable}')"
echo.

echo 检查已安装的包...
pip show xtquant
echo.

echo ========================================
echo 运行 pytest 测试（带详细输出）
echo ========================================
echo.

REM 运行 pytest，显示详细输出和打印内容
pytest tests\integration\miniqmt\test_miniqmt_data.py -v -s --tb=short

if errorlevel 1 (
    echo.
    echo 测试运行失败，请检查错误信息
) else (
    echo.
    echo 测试运行成功！
)

echo.
echo 查看日志文件: .quantx-dev\logs\tests\integration\miniqmt\test_miniqmt_data.log
pause
