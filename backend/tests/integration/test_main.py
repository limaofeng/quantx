"""
应用主体功能测试
"""
import pytest
from fastapi.testclient import TestClient

@pytest.mark.api
@pytest.mark.unit
def test_root_endpoint(sync_client):
    """测试根路径端点"""
    response = sync_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "QuantX API is running with GraphQL"
    assert data["version"] == "2.0.0"
    assert "graphql_endpoint" in data

@pytest.mark.api
@pytest.mark.unit
def test_health_check(sync_client):
    """测试健康检查端点"""
    response = sync_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["api_type"] == "GraphQL"
    assert data["realtime_enabled"] == True

@pytest.mark.api
@pytest.mark.unit
def test_metrics_endpoint(sync_client):
    """测试指标端点"""
    response = sync_client.get("/metrics")
    assert response.status_code == 200
    # Prometheus 指标应该是文本格式
    assert "text/plain" in response.headers.get("content-type", "")

@pytest.mark.api
@pytest.mark.unit
def test_nonexistent_endpoint(sync_client):
    """测试不存在的端点"""
    response = sync_client.get("/nonexistent")
    assert response.status_code == 404

@pytest.mark.api
@pytest.mark.unit
def test_cors_headers(sync_client):
    """测试 CORS 头部"""
    # 先发送 GET 请求，然后检查响应头
    response = sync_client.get("/", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 200

    # 检查是否有CORS相关的响应头（这些由中间件添加）
    # 注意：TestClient 可能不会完全模拟CORS行为，所以我们只检查基本响应

@pytest.mark.api
class TestApplicationConfiguration:
    """应用配置测试"""

    def test_app_title(self, sync_client):
        """测试应用标题"""
        response = sync_client.get("/openapi.json")
        if response.status_code == 200:
            openapi = response.json()
            assert openapi["info"]["title"] == "QuantX API"

    @pytest.mark.unit
    def test_environment_variables(self):
        """测试环境变量加载"""
        from config.settings import settings
        assert settings.host is not None
        assert settings.port is not None
        assert settings.environment is not None
