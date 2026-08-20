"""Read-only access to persisted board-replay scenario results."""

from __future__ import annotations

from typing import Any, Iterable

from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.repositories.backtest_repository import BacktestRepository


class LimitUpBoardReplayResultService:
  async def load_many(
    self,
    backtest_ids: Iterable[str],
  ) -> dict[str, dict[str, Any]]:
    normalized = list(
      dict.fromkeys(str(item).strip() for item in backtest_ids if str(item).strip())
    )
    if not normalized:
      return {}
    async for db in get_async_db():
      rows = await BacktestRepository(db).get_backtests_by_ids(normalized)
      return {
        backtest_id: self._scenario_result(getattr(row, "metrics", None))
        for backtest_id, row in rows.items()
      }
    return {}

  @staticmethod
  def _scenario_result(metrics: Any) -> dict[str, Any]:
    payload = dict(metrics or {}) if isinstance(metrics, dict) else {}
    result = payload.get("limit_up_board_replay")
    return dict(result or {}) if isinstance(result, dict) else {}


limit_up_board_replay_result_service = LimitUpBoardReplayResultService()


__all__ = [
  "LimitUpBoardReplayResultService",
  "limit_up_board_replay_result_service",
]
