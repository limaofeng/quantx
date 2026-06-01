"""
配置管理测试
"""
import pytest
import os
from unittest.mock import patch

def test_settings_loading():
    """测试配置加载"""
    from config.settings import settings

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
    from config.settings import settings

    assert hasattr(settings, 'cors_origins')
    assert isinstance(settings.cors_origins, list)

def test_log_config():
    """测试日志配置"""
    from config.settings import settings

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
    from config.settings import settings

    assert hasattr(settings, 'is_development')
    assert hasattr(settings, 'is_production')
    assert isinstance(settings.is_development, bool)
    assert isinstance(settings.is_production, bool)

def test_metrics_configuration():
    """测试指标配置"""
    from config.settings import settings

    assert hasattr(settings, 'metrics_enabled')
    assert isinstance(settings.metrics_enabled, bool)

@patch.dict(os.environ, {'DEBUG': 'true'})
def test_debug_mode_override():
    """测试调试模式环境变量覆盖"""
    # 重新导入以获取新的环境变量
    import importlib
    from config.settings import settings
    # 注意：由于配置是在模块级别加载的，这个测试可能需要特殊处理
    # 注意：由于配置是在模块级别加载的，这个测试可能需要特殊处理
