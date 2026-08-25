"""No-signal harness used to replay Engine-owned A-share exit plans."""

from __future__ import annotations

from typing import Any, Dict

from quantx_domain.enums import StrategyInstrumentScope
from quantx_domain.schemas import ParameterSchema
from quantx_domain.strategies.base import StrategyBase, StrategyInput, StrategyOutput


class AshareExitPlanReplayHarnessStrategy(StrategyBase):
  """Keep the shared Engine exit-plan runtime active without entry signals."""

  INSTRUMENT_SCOPE = StrategyInstrumentScope.SINGLE

  @property
  def name(self) -> str:
    return "A股卖出计划历史回放适配器"

  @property
  def version(self) -> str:
    return "1.0.0"

  @property
  def description(self) -> str:
    return "只承载历史持仓与退出计划，由 Engine 公共退出链路产生卖出意图。"

  @classmethod
  def get_parameter_schema(cls) -> ParameterSchema:
    # The replay service owns and validates the audit payload. The harness does
    # not interpret it, so duplicating that evolving contract here would create
    # a second source of truth.
    return ParameterSchema(type="object", additionalProperties=True)

  @classmethod
  def get_data_requirements(cls) -> Dict[str, Any]:
    return {"use_tick_data": True, "periods": []}

  async def on_init(self) -> None:
    if len(self.context.instruments) != 1:
      raise ValueError("exit plan replay requires exactly one instrument")

  async def step(self, input: StrategyInput) -> StrategyOutput:
    del input
    return StrategyOutput()

  async def on_stop(self) -> None:
    return None
