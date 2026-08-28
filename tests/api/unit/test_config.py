"""
配置管理测试
"""
import os
from unittest.mock import patch

import pytest


def test_settings_loading():
    """测试配置加载"""
    from quantx_infrastructure.config.settings import settings

    # 测试基本配置项
    assert hasattr(settings, 'host')
    assert hasattr(settings, 'port')
    assert hasattr(settings, 'debug')
    assert hasattr(settings, 'environment')

    # 测试默认值
    assert settings.host in ['0.0.0.0', 'localhost', '127.0.0.1']
    assert isinstance(settings.port, int)
    assert settings.port > 0

def test_cors_origins():
    """测试 CORS 配置"""
    from quantx_infrastructure.config.settings import settings

    assert hasattr(settings, 'cors_origins')
    assert isinstance(settings.cors_origins, list)

def test_log_config():
    """测试日志配置"""
    from quantx_infrastructure.config.settings import settings

    log_config = settings.get_log_config()
    assert 'version' in log_config
    assert 'formatters' in log_config
    assert 'handlers' in log_config
    assert 'root' in log_config  # 修正：使用 'root' 而不是 'loggers'

    # 验证日志配置结构
    assert log_config['version'] == 1
    assert 'default' in log_config['formatters']
    assert 'default' in log_config['handlers']
    assert 'level' in log_config['root']

def test_environment_detection():
    """测试环境检测"""
    from quantx_infrastructure.config.settings import settings

    assert hasattr(settings, 'is_development')
    assert hasattr(settings, 'is_production')
    assert isinstance(settings.is_development, bool)
    assert isinstance(settings.is_production, bool)

def test_metrics_configuration():
    """测试指标配置"""
    from quantx_infrastructure.config.settings import settings

    assert hasattr(settings, 'metrics_enabled')
    assert isinstance(settings.metrics_enabled, bool)

@patch.dict(os.environ, {'DEBUG': 'true'})
def test_debug_mode_override():
    """测试调试模式环境变量覆盖"""
    # 重新导入以获取新的环境变量
    # 注意：由于配置是在模块级别加载的，这个测试可能需要特殊处理
    # 注意：由于配置是在模块级别加载的，这个测试可能需要特殊处理


def _production_settings(**overrides):
    from quantx_infrastructure.config.settings import Settings

    values = {
        "ENV": "production",
        "host": "0.0.0.0",
        "port": 18081,
        "public_url": "https://quantx.example.com",
        "database_url": "postgresql+asyncpg://user:password@127.0.0.1/quantx",
        "debug": False,
        "graphql_debug": False,
        "graphql_introspection": False,
        "graphql_playground": False,
        "mock_data_enabled": False,
        "secret_key": "s" * 64,
        "auth_web_cookie_secure": True,
        "cors_origins": ["https://quantx.example.com"],
        "auth_web_allowed_origins": ["https://quantx.example.com"],
        "redis_url": "redis://127.0.0.1:6379/0",
        "influxdb_host": "http://127.0.0.1:8086",
        "influxdb_token": "test-token",
        "influxdb_database": "quantx_market_data",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_safe_production_configuration_passes_fail_closed_validator():
    _production_settings().validate_production()


def test_empty_log_file_uses_stdout_without_configuring_a_file_handler():
    configured = _production_settings(log_file="")

    logging_config = configured.get_log_config()

    assert logging_config["root"]["handlers"] == ["default"]
    assert "file" not in logging_config["handlers"]


@pytest.mark.parametrize(
    "public_url",
    (
        "http://quantx.example.com",
        "https://*",
        "https://0.0.0.0",
        "https://127.0.0.1:8080",
        "https://user:password@quantx.example.com",
        "https://quantx.example.com:notaport",
        "https://quantx.example.com/deployment",
    ),
)
def test_production_rejects_non_public_service_origins(public_url):
    configured = _production_settings(
        public_url=public_url,
        cors_origins=[public_url],
        auth_web_allowed_origins=[public_url],
    )

    with pytest.raises(RuntimeError, match="external HTTPS service origin"):
        configured.validate_production()


def test_production_rejects_loopback_api_binding():
    configured = _production_settings(host="127.0.0.1")

    with pytest.raises(RuntimeError, match="pod interface 0.0.0.0:18081"):
        configured.validate_production()


def test_manual_trade_permission_is_never_granted_by_default():
    configured = _production_settings()

    assert "trade:manual" not in configured.auth_bootstrap_permissions
    assert "trade:direct" not in configured.auth_bootstrap_permissions


def test_unsafe_production_configuration_reports_every_gate_without_secrets():
    secret = "do-not-log-this-secret"
    configured = _production_settings(
        public_url="http://0.0.0.0:8080",
        database_url="sqlite:///quantx.db",
        debug=True,
        graphql_introspection=True,
        mock_data_enabled=True,
        secret_key=secret,
        auth_web_cookie_secure=False,
        redis_url="",
    )

    with pytest.raises(RuntimeError) as error:
        configured.validate_production()

    message = str(error.value)
    assert "PostgreSQL" in message
    assert "mock data" in message
    assert "SECRET_KEY" in message
    assert secret not in message
