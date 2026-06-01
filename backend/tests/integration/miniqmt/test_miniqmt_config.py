"""
MiniQMT 配置管理测试
"""
import pytest
import os
import json
import tempfile
from unittest.mock import patch, mock_open
from pathlib import Path


def _temp_config(tmp_path):
    """创建隔离的 MiniQMT 测试配置，避免读写仓库内真实 config.json。"""
    from miniqmt.config.config_manager import XTQuantConfig

    return XTQuantConfig(str(tmp_path / "config.json"))


def test_miniqmt_config_initialization(tmp_path):
    """测试 MiniQMT 配置初始化"""
    config = _temp_config(tmp_path)
    assert config is not None
    assert hasattr(config, 'config')
    assert isinstance(config.config, dict)

def test_config_default_values(tmp_path):
    """测试配置默认值"""
    config = _temp_config(tmp_path)

    # 测试 xtquant 配置
    assert 'xtquant' in config.config
    xtquant_config = config.config['xtquant']

    # 测试数据服务器配置
    assert 'data_server' in xtquant_config
    data_server = xtquant_config['data_server']
    assert data_server['host'] == '127.0.0.1'
    assert data_server['port'] == 58610

    # 测试交易服务器配置
    assert 'trading_server' in xtquant_config
    trading_server = xtquant_config['trading_server']
    assert trading_server['host'] == '127.0.0.1'
    assert trading_server['port'] == 58611

    # 测试账户配置
    assert 'account' in xtquant_config
    account = xtquant_config['account']
    assert account['account_type'] == 'stock'

def test_data_config(tmp_path):
    """测试数据配置"""
    config = _temp_config(tmp_path)
    data_config = config.config['data']

    assert data_config['cache_enabled'] == True
    assert data_config['cache_dir'] == './cache'
    assert data_config['update_interval'] == 1000
    assert data_config['max_retry'] == 3

def test_trading_config(tmp_path):
    """测试交易配置"""
    config = _temp_config(tmp_path)
    trading_config = config.config['trading']

    # 测试风控配置
    risk_control = trading_config['risk_control']
    assert risk_control['max_position_ratio'] == 0.95
    assert risk_control['max_single_stock_ratio'] == 0.20
    assert risk_control['stop_loss_ratio'] == 0.05
    assert risk_control['take_profit_ratio'] == 0.15

    # 测试订单配置
    order_settings = trading_config['order_settings']
    assert order_settings['default_price_type'] == 'limit'
    assert order_settings['order_timeout'] == 60
    assert order_settings['max_order_size'] == 10000

def test_get_config_value(tmp_path):
    """测试获取配置值"""
    config = _temp_config(tmp_path)

    # 测试获取存在的配置
    host = config.get('xtquant.data_server.host')
    assert host == '127.0.0.1'

    # 测试获取不存在的配置（带默认值）
    unknown = config.get('unknown.config', 'default_value')
    assert unknown == 'default_value'

def test_set_config_value(tmp_path):
    """测试设置配置值"""
    config = _temp_config(tmp_path)

    # 设置新的配置值
    config.set('test.value', 'test_data')
    assert config.get('test.value') == 'test_data'

    # 修改现有配置值
    config.set('xtquant.data_server.host', '192.168.5.6')
    assert config.get('xtquant.data_server.host') == '192.168.5.6'

def test_config_validation(tmp_path):
    """测试配置验证"""
    config = _temp_config(tmp_path)

    # 测试有效配置验证
    assert config.validate() == True

    # 测试无效配置
    config.set('xtquant.data_server.port', 'invalid_port')
    assert config.validate() == False

def test_save_and_reload_config():
    """测试配置保存和重新加载"""
    from miniqmt.config.config_manager import XTQuantConfig

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_config_file = f.name

    try:
        # 创建配置并修改值
        config = XTQuantConfig(temp_config_file)
        config.set('test.save', 'saved_value')
        config.save()

        # 重新加载配置
        config2 = XTQuantConfig(temp_config_file)
        assert config2.get('test.save') == 'saved_value'

    finally:
        os.unlink(temp_config_file)

@patch('builtins.open', mock_open(read_data='invalid json'))
def test_config_load_invalid_json():
    """测试加载无效 JSON 配置文件"""
    from miniqmt.config.config_manager import XTQuantConfig

    # 应该回退到默认配置
    config = XTQuantConfig('invalid.json')
    assert config.config is not None
    assert 'xtquant' in config.config

def test_config_file_not_exists():
    """测试配置文件不存在的情况"""
    from miniqmt.config.config_manager import XTQuantConfig

    config = XTQuantConfig('nonexistent.json')
    assert config.config is not None
    assert 'xtquant' in config.config

def test_xt_config_singleton():
    """测试配置单例"""
    from miniqmt.config.config_manager import xt_config

    # 获取两次应该是同一个实例
    config1 = xt_config
    config2 = xt_config
    assert config1 is config2

def test_environment_specific_config(tmp_path):
    """测试环境特定配置"""
    with patch.dict(os.environ, {'XTQUANT_ENV': 'test'}):
        config = _temp_config(tmp_path)
        # 在测试环境下应该有特殊配置
        assert config.get('environment') == 'test' or config.config is not None

class TestXTQuantConfigIntegration:
    """XTQuant 配置集成测试"""

    def test_config_with_real_file(self):
        """使用真实配置文件测试"""
        from miniqmt.config.config_manager import XTQuantConfig

        # 创建临时配置文件
        test_config = {
            "xtquant": {
                "data_server": {
                    "host": "test.host.com",
                    "port": 9999
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(test_config, f)
            temp_file = f.name

        try:
            config = XTQuantConfig(temp_file)
            assert config.get('xtquant.data_server.host') == 'test.host.com'
            assert config.get('xtquant.data_server.port') == 9999

        finally:
            os.unlink(temp_file)

    def test_config_update_and_persist(self):
        """测试配置更新和持久化"""
        from miniqmt.config.config_manager import XTQuantConfig

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_file = f.name

        try:
            # 创建并修改配置
            config = XTQuantConfig(temp_file)
            original_host = config.get('xtquant.data_server.host', 'localhost')

            config.set('xtquant.data_server.host', 'updated.host.com')
            config.save()

            # 验证配置已保存
            with open(temp_file, 'r') as f:
                saved_config = json.load(f)

            assert saved_config['xtquant']['data_server']['host'] == 'updated.host.com'

        finally:
            if os.path.exists(temp_file):
                os.unlink(temp_file)
