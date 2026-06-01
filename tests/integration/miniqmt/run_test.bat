@echo off
REM 使用当前 Python 环境运行测试；可通过 QUANTX_CONDA_ENV 指定 conda 环境

echo ========================================
if defined QUANTX_CONDA_ENV (
    echo 使用 conda 环境: %QUANTX_CONDA_ENV%
) else (
    echo 使用当前 Python 环境
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

echo 当前环境:
python --version
echo.

echo ========================================
echo 运行 tick 数据测试
echo ========================================
echo.

REM 运行测试
python tests\integration\miniqmt\test_miniqmt_data.py

if errorlevel 1 (
    echo.
    echo 测试运行失败，请检查错误信息
) else (
    echo.
    echo 测试运行完成！
)

echo.
echo 查看日志文件: tests\integration\miniqmt\test_miniqmt_data.log
pause
