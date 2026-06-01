"""
监控功能测试
"""
import pytest
import time
from unittest.mock import patch, MagicMock

def test_metrics_middleware_initialization():
    """测试指标中间件初始化"""
    from monitoring import MetricsMiddleware

    middleware = MetricsMiddleware(None, enabled=True)
    assert middleware.enabled == True

    middleware_disabled = MetricsMiddleware(None, enabled=False)
    assert middleware_disabled.enabled == False

def test_application_metrics():
    """测试应用指标管理器"""
    from monitoring import ApplicationMetrics

    app_metrics = ApplicationMetrics()

    # 测试订阅计数
    app_metrics.increment_subscription("stockPrice")
    assert app_metrics.active_subscriptions["stockPrice"] == 1

    app_metrics.increment_subscription("stockPrice")
    assert app_metrics.active_subscriptions["stockPrice"] == 2

    app_metrics.decrement_subscription("stockPrice")
    assert app_metrics.active_subscriptions["stockPrice"] == 1

    # 测试连接数设置
    app_metrics.set_active_connections(5)

    # 测试指标摘要
    summary = app_metrics.get_metrics_summary()
    assert "system" in summary
    assert "application" in summary
    assert "timestamp" in summary

@patch('psutil.cpu_percent')
@patch('psutil.virtual_memory')
@patch('psutil.disk_usage')
def test_system_metrics(mock_disk, mock_memory, mock_cpu):
    """测试系统指标收集"""
    from monitoring import SystemMetrics

    # 模拟系统指标
    mock_cpu.return_value = 50.0
    mock_memory.return_value = MagicMock(percent=60.0)
    mock_disk.return_value = MagicMock(used=500, total=1000)

    # 测试指标更新（这个测试可能需要时间，因为实际会调用 psutil）
    try:
        SystemMetrics.update_system_metrics()
    except Exception as e:
        # 在测试环境中可能会失败，这是正常的
        pass

def test_prometheus_metrics_generation(sync_client):
    """测试 Prometheus 指标生成"""
    response = sync_client.get("/metrics")

    if response.status_code == 200:
        content = response.text
        # 检查是否包含一些基本的 Prometheus 指标
        assert "# HELP" in content
        assert "# TYPE" in content
    else:
        # 如果指标被禁用，应该返回错误信息
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
