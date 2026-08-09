"""
参数管理系统测试
"""

import json
import os
import tempfile
from unittest.mock import patch

import pytest
import yaml
from quantx_infrastructure.core.config import COMMON_PARAMETER_SCHEMAS, ParameterManager


class TestParameterManager:
  """参数管理器测试"""

  @pytest.fixture
  def param_manager(self):
    """创建测试用参数管理器"""
    pm = ParameterManager()
    # 注册测试schema
    test_schema = {
      "type": "object",
      "properties": {
        "param1": {"type": "string", "default": "default1"},
        "param2": {"type": "number", "default": 10.0}
      },
      "required": ["param1"]
    }
    pm.register_schema("test_strategy", test_schema)
    return pm

  def test_add_source(self, param_manager):
    """测试添加参数源"""
    data = {"test_strategy": {"param1": "value1"}}
    param_manager.add_source("test_source", 1, data)

    assert len(param_manager.sources) == 1
    assert param_manager.sources[0].name == "test_source"
    assert param_manager.sources[0].priority == 1
    assert param_manager.sources[0].data == data

  def test_source_priority_sorting(self, param_manager):
    """测试参数源优先级排序"""
    param_manager.add_source("low_priority", 1, {})
    param_manager.add_source("high_priority", 3, {})
    param_manager.add_source("medium_priority", 2, {})

    # 应该按优先级排序（低到高）
    priorities = [source.priority for source in param_manager.sources]
    assert priorities == [1, 2, 3]

  def test_merge_parameters_simple(self, param_manager):
    """测试简单参数合并"""
    # 添加多个参数源
    param_manager.add_source("base", 1, {
      "test_strategy": {"param1": "base_value", "param2": 5.0}
    })
    param_manager.add_source("override", 2, {
      "test_strategy": {"param1": "override_value"}
    })

    result = param_manager.merge_parameters("test_strategy")

    # 高优先级应该覆盖低优先级
    assert result["param1"] == "override_value"
    assert result["param2"] == 5.0

  def test_merge_parameters_with_runtime_override(self, param_manager):
    """测试运行时参数覆盖"""
    param_manager.add_source("base", 1, {
      "test_strategy": {"param1": "base_value", "param2": 5.0}
    })

    runtime_params = {"param1": "runtime_value", "param3": "new_param"}
    result = param_manager.merge_parameters("test_strategy", runtime_params)

    assert result["param1"] == "runtime_value"  # 运行时覆盖
    assert result["param2"] == 5.0  # 保持原值
    assert result["param3"] == "new_param"  # 新参数

  def test_deep_merge(self, param_manager):
    """测试深度合并"""
    param_manager.add_source("base", 1, {
      "test_strategy": {
        "nested": {"a": 1, "b": 2},
        "simple": "value"
      }
    })
    param_manager.add_source("override", 2, {
      "test_strategy": {
        "nested": {"b": 3, "c": 4}
      }
    })

    result = param_manager.merge_parameters("test_strategy")

    assert result["nested"]["a"] == 1  # 保持原值
    assert result["nested"]["b"] == 3  # 被覆盖
    assert result["nested"]["c"] == 4  # 新值
    assert result["simple"] == "value"  # 保持原值

  def test_validate_parameters_success(self, param_manager):
    """测试参数验证成功"""
    params = {"param1": "test_value", "param2": 15.0}
    result = param_manager.validate_parameters("test_strategy", params)
    assert result == params

  def test_validate_parameters_failure(self, param_manager):
    """测试参数验证失败"""
    # 缺少必需参数
    params = {"param2": 15.0}
    with pytest.raises(ValueError, match="参数验证失败"):
      param_manager.validate_parameters("test_strategy", params)

  def test_validate_parameters_no_schema(self, param_manager):
    """测试无schema时的参数验证"""
    params = {"any_param": "any_value"}
    result = param_manager.validate_parameters("unknown_strategy", params)
    assert result == params  # 无schema时直接返回

  def test_get_parameter_template(self, param_manager):
    """测试获取参数模板"""
    template = param_manager.get_parameter_template("test_strategy")

    assert "param1" in template
    assert "param2" in template
    assert template["param1"] == "default1"
    assert template["param2"] == 10.0

  def test_get_parameter_template_no_schema(self, param_manager):
    """测试无schema时获取参数模板"""
    template = param_manager.get_parameter_template("unknown_strategy")
    assert template == {}

  def test_load_from_env(self, param_manager):
    """测试从环境变量加载"""
    env_vars = {
      "STRATEGY_test_param": "env_value",
      "STRATEGY_numeric_param": "123.45",
      "STRATEGY_json_param": '{"key": "value"}',
      "OTHER_PARAM": "should_be_ignored"
    }

    with patch.dict(os.environ, env_vars):
      param_manager.load_from_env("STRATEGY_", priority=2)

    # 检查是否正确加载
    assert len(param_manager.sources) == 1
    source = param_manager.sources[0]
    assert source.name == "environment"
    assert source.priority == 2

    # 检查参数解析
    assert source.data["test_param"] == "env_value"
    assert source.data["numeric_param"] == 123.45
    assert source.data["json_param"] == {"key": "value"}
    assert "other_param" not in source.data

  def test_clear_sources(self, param_manager):
    """测试清除参数源"""
    param_manager.add_source("test", 1, {})
    assert len(param_manager.sources) == 1

    param_manager.clear_sources()
    assert len(param_manager.sources) == 0

  def test_get_statistics(self, param_manager):
    """测试获取统计信息"""
    param_manager.add_source("source1", 1, {"strategy1": {}, "strategy2": {}})
    param_manager.add_source("source2", 2, {"strategy1": {}})

    stats = param_manager.get_statistics()

    assert stats["sources_count"] == 2
    assert stats["schemas_count"] == 1  # test_strategy
    assert len(stats["sources"]) == 2
    assert stats["sources"][0]["strategies_count"] == 2
    assert stats["sources"][1]["strategies_count"] == 1


class TestParameterManagerFileOperations:
  """参数管理器文件操作测试"""

  @pytest.fixture
  def param_manager(self):
    return ParameterManager()

  @pytest.fixture
  def temp_json_file(self):
    """创建临时JSON配置文件"""
    config_data = {
      "strategy1": {
        "param1": "json_value1",
        "param2": 100
      },
      "strategy2": {
        "param1": "json_value2"
      }
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
      json.dump(config_data, f)
      f.flush()
      yield f.name

    os.unlink(f.name)

  @pytest.fixture
  def temp_yaml_file(self):
    """创建临时YAML配置文件"""
    config_data = {
      "strategy1": {
        "param1": "yaml_value1",
        "param2": 200
      }
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
      yaml.dump(config_data, f)
      f.flush()
      yield f.name

    os.unlink(f.name)

  def test_load_from_json_file(self, param_manager, temp_json_file):
    """测试从JSON文件加载"""
    param_manager.load_from_file(temp_json_file, priority=1)

    assert len(param_manager.sources) == 1
    source = param_manager.sources[0]
    assert source.name == temp_json_file
    assert source.data["strategy1"]["param1"] == "json_value1"
    assert source.data["strategy1"]["param2"] == 100

  def test_load_from_yaml_file(self, param_manager, temp_yaml_file):
    """测试从YAML文件加载"""
    param_manager.load_from_file(temp_yaml_file, priority=1)

    assert len(param_manager.sources) == 1
    source = param_manager.sources[0]
    assert source.data["strategy1"]["param1"] == "yaml_value1"
    assert source.data["strategy1"]["param2"] == 200

  def test_load_from_nonexistent_file(self, param_manager):
    """测试加载不存在的文件"""
    with pytest.raises(FileNotFoundError):
      param_manager.load_from_file("nonexistent.json", priority=1)

  def test_load_from_unsupported_file(self, param_manager):
    """测试加载不支持的文件格式"""
    with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
      f.write(b"some text")
      temp_file = f.name

    try:
      with pytest.raises(ValueError, match="不支持的文件格式"):
        param_manager.load_from_file(temp_file, priority=1)
    finally:
      os.unlink(temp_file)

  def test_save_parameters_json(self, param_manager):
    """测试保存参数到JSON文件"""
    params = {"param1": "test_value", "param2": 123}

    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
      temp_file = f.name

    try:
      param_manager.save_parameters(temp_file, "test_strategy", params)

      # 验证文件内容
      with open(temp_file, 'r') as f:
        saved_data = json.load(f)

      assert saved_data["test_strategy"] == params
    finally:
      os.unlink(temp_file)

  def test_save_parameters_yaml(self, param_manager):
    """测试保存参数到YAML文件"""
    params = {"param1": "test_value", "param2": 123}

    with tempfile.NamedTemporaryFile(suffix='.yaml', delete=False) as f:
      temp_file = f.name

    try:
      param_manager.save_parameters(temp_file, "test_strategy", params)

      # 验证文件内容
      with open(temp_file, 'r') as f:
        saved_data = yaml.safe_load(f)

      assert saved_data["test_strategy"] == params
    finally:
      os.unlink(temp_file)

  def test_save_parameters_update_existing(self, param_manager):
    """测试更新现有配置文件"""
    # 先创建一个包含现有配置的文件
    existing_data = {"existing_strategy": {"param": "value"}}

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
      json.dump(existing_data, f)
      temp_file = f.name

    try:
      # 添加新策略配置
      new_params = {"param1": "new_value"}
      param_manager.save_parameters(temp_file, "new_strategy", new_params)

      # 验证两个策略配置都存在
      with open(temp_file, 'r') as f:
        saved_data = json.load(f)

      assert "existing_strategy" in saved_data
      assert "new_strategy" in saved_data
      assert saved_data["new_strategy"] == new_params
      assert saved_data["existing_strategy"] == existing_data["existing_strategy"]
    finally:
      os.unlink(temp_file)


class TestCommonParameterSchemas:
  """常用参数schema测试"""

  def test_common_schemas_existence(self):
    """测试常用schema的存在性"""
    assert "base_strategy" in COMMON_PARAMETER_SCHEMAS
    assert "ma_cross_strategy" in COMMON_PARAMETER_SCHEMAS
    assert "rsi_strategy" in COMMON_PARAMETER_SCHEMAS

  def test_base_strategy_schema(self):
    """测试基础策略schema"""
    schema = COMMON_PARAMETER_SCHEMAS["base_strategy"]

    assert schema["type"] == "object"
    assert "initial_capital" in schema["properties"]
    assert "initial_capital" in schema["required"]

    # 测试默认值
    assert schema["properties"]["initial_capital"]["default"] == 1000000
    assert schema["properties"]["max_position_pct"]["default"] == 0.1

  def test_ma_cross_strategy_schema(self):
    """测试均线交叉策略schema"""
    schema = COMMON_PARAMETER_SCHEMAS["ma_cross_strategy"]

    assert "short_period" in schema["properties"]
    assert "long_period" in schema["properties"]
    assert "ma_type" in schema["properties"]

    # 测试枚举值
    assert schema["properties"]["ma_type"]["enum"] == ["SMA", "EMA", "WMA"]

  def test_rsi_strategy_schema(self):
    """测试RSI策略schema"""
    schema = COMMON_PARAMETER_SCHEMAS["rsi_strategy"]

    assert "rsi_period" in schema["properties"]
    assert "oversold_level" in schema["properties"]
    assert "overbought_level" in schema["properties"]

    # 测试数值范围
    assert schema["properties"]["oversold_level"]["maximum"] == 50
    assert schema["properties"]["overbought_level"]["minimum"] == 50
