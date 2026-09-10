"""Configuration freezing must not silently change an explicit scorer mode."""

import pytest
from quantx_engine.t_assistant_paper_shadow_supervisor import (
  TAssistantPaperShadowSupervisor,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantDecisionCycleRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from sqlalchemy import func, select

from tests.engine.unit.test_t_assistant_paper_shadow_runtime import (
  _reconcile_cursor_probe,
  sessions,
)

_FIXTURES = sessions


def freeze(settings):
  return TAssistantPaperShadowSupervisor._config_version(TTradeGlobalConfig(
    id="model-config", account_id="isolated-account", config_version=1,
    settings=settings, ignored_stock_codes=[],
  ))


@pytest.mark.parametrize("mode", ["ACTIVE", "SHADOW"])
@pytest.mark.parametrize("binding", [None, {}, [], "missing"])
def test_explicit_model_mode_without_binding_is_rejected(mode, binding):
  with pytest.raises(ValueError, match="T_MODEL_CONFIG_BINDING_INVALID"):
    freeze({"scorer_mode": mode, "model_runtime_binding": binding})


@pytest.mark.parametrize("mode", ["unknown", "", None, 1])
def test_invalid_explicit_mode_is_not_frozen_as_rule_only(mode):
  with pytest.raises(ValueError, match="T_MODEL_CONFIG_MODE_INVALID"):
    freeze({"scorer_mode": mode})


def test_rule_only_cannot_hide_a_model_binding():
  with pytest.raises(ValueError, match="T_MODEL_CONFIG_BINDING_INVALID"):
    freeze({"scorer_mode": "RULE_ONLY", "model_runtime_binding": {"binding_hash": "a" * 64}})


@pytest.mark.parametrize("mode", ["ACTIVE", "SHADOW"])
def test_scored_configuration_preserves_mode_and_binding(mode):
  binding = {"binding_hash": "a" * 64}
  result = freeze({"scorer_mode": mode, "model_runtime_binding": binding})
  assert result.scorer_mode.value == mode and result.model_runtime_binding == binding


def test_absent_mode_still_defaults_to_rule_only():
  result = freeze({})
  assert result.scorer_mode.value == "RULE_ONLY" and result.model_runtime_binding is None


async def test_invalid_active_refresh_unbinds_old_rule_only_source(sessions):
  config, hub, supervisor, universe, execution_id = await _reconcile_cursor_probe(sessions)
  try:
    async with sessions() as db, db.begin():
      head = await db.get(TTradeGlobalConfig, config.id)
      head.settings = {**head.settings, "scorer_mode": "ACTIVE"}
      config = head
      before = await db.scalar(select(func.count(TAssistantDecisionCycleRecord.cycle_id)))
    with pytest.raises(ValueError, match="T_MODEL_CONFIG_BINDING_INVALID"):
      await supervisor.reconcile(config=config, universe=universe)
    assert execution_id not in supervisor._bindings
    await hub.emit(5)
    async with sessions() as db:
      assert await db.scalar(select(func.count(TAssistantDecisionCycleRecord.cycle_id))) == before
  finally:
    await supervisor.stop()
