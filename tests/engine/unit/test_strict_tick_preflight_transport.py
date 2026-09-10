"""Strict preflight must never promote partial transport results to completeness."""

import asyncio
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from quantx_engine.strategy_manager import StrategyManager


@pytest.mark.parametrize("kind", ["error", "cancel", "capacity"])
async def test_partial_read_is_unavailable_and_closes_stream(monkeypatch, kind):
  StrategyManager._instance = None
  manager = StrategyManager()
  closed = []

  class Reader:
    async def iter_tick_pages(self, **kwargs):
      try:
        assert kwargs["page_size"] == 1000
        assert kwargs["max_pages"] == 200
        assert kwargs["max_source_ticks"] == 200_000
        yield [
          SimpleNamespace(
            time=datetime(2026, 8, 3, 9, 30), amount=100, volume=1, pvolume=100
          )
        ]
        if kind == "cancel":
          raise asyncio.CancelledError()
        raise RuntimeError("transport lost after first page")
      finally:
        closed.append(True)

  monkeypatch.setattr(
    "quantx_engine.strategy_manager.LocalHistoricalTickReader", Reader
  )
  if kind == "capacity":
    monkeypatch.setattr(
      "quantx_engine.strategy_manager._STRICT_TICK_PREFLIGHT_MAX_PROJECTED_BYTES", 1
    )
  try:
    if kind == "cancel":
      with pytest.raises(asyncio.CancelledError):
        await manager._inspect_strict_tick_replay_day("600887.SH", date(2026, 8, 3))
    else:
      result = await manager._inspect_strict_tick_replay_day(
        "600887.SH", date(2026, 8, 3)
      )
      assert result["complete"] is False
      assert result["classification"] == "UNAVAILABLE"
      assert result["reason_codes"] == ["TICK_QUERY_FAILED"]
      assert result["query_error_type"] == (
        "HistoricalTickPaginationError" if kind == "capacity" else "RuntimeError"
      )
    assert closed == [True]
  finally:
    StrategyManager._instance = None
