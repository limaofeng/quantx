"""
中间件功能测试
测试错误处理、请求日志和指标收集功能
"""

import pytest


@pytest.mark.middleware
class TestMiddleware:
    """中间件测试类"""

    @pytest.mark.unit
    def test_error_handler_middleware_404(self, sync_client):
        """测试错误处理中间件 - 404错误"""
        response = sync_client.get("/nonexistent-endpoint")
        assert response.status_code == 404

    @pytest.mark.unit
    def test_error_handler_middleware_method_not_allowed(self, sync_client):
        """测试错误处理中间件 - 方法不允许"""
        response = sync_client.post("/health")  # health endpoint 只允许 GET
        assert response.status_code == 405

    @pytest.mark.integration
    def test_request_logging_middleware(self, sync_client):
        """测试请求日志中间件"""
        # 发送几个请求来触发日志记录
        response1 = sync_client.get("/health/live")
        response2 = sync_client.get("/")

        assert response1.status_code == 200
        assert response2.status_code == 200

    @pytest.mark.integration
    def test_metrics_middleware_collection(self, sync_client):
        """测试指标收集中间件"""
        # 发送请求来触发指标收集
        response = sync_client.get("/health/live")
        assert response.status_code == 200

        # 检查指标端点
        metrics_response = sync_client.get("/metrics")
        if metrics_response.status_code == 200:
            content = metrics_response.text
            # 检查是否包含 HTTP 请求指标
            assert "http_requests_total" in content or "# HELP" in content

@pytest.mark.middleware
class TestErrorHandling:
    """错误处理测试"""

    @pytest.mark.api
    @pytest.mark.graphql
    def test_graphql_validation_error(self, sync_client):
        """测试 GraphQL 验证错误"""
        query = {
            "query": "query { invalidField }"
        }
        response = sync_client.post("/graphql", json=query)
        # GraphQL 验证错误通常返回 200 但包含错误信息
        if response.status_code == 200:
            data = response.json()
            assert "errors" in data
        else:
            # 如果返回其他状态码，确保是预期的错误处理
            assert response.status_code in [400, 422]

    @pytest.mark.api
    def test_malformed_json_request(self, sync_client):
        """测试格式错误的 JSON 请求"""
        response = sync_client.post(
            "/graphql",
            data="invalid json",
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code in [400, 422]

@pytest.mark.asyncio
@pytest.mark.middleware
@pytest.mark.integration
async def test_async_middleware_functionality():
    """测试异步中间件功能"""
    # 这里可以添加更复杂的异步测试
    pass


if __name__ == "__main__":
    pytest.main([__file__])
