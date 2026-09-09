from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from quantx_infrastructure.services import trading_time_service
from quantx_research import next_day_selection_training as training
from quantx_research import runner
from quantx_research.next_day_selection_config import load_next_day_selection_config


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [True, False])
async def test_external_certification_source_never_opens_default_database(
  monkeypatch, tmp_path, complete
):
  config = load_next_day_selection_config(
    Path(__file__).resolve().parents[2]
    / "apps/research/configs/next_day_selection_v1.yaml"
  )
  source = object()
  end = config.data.date_range[1] + timedelta(days=1)
  calendar = SimpleNamespace(get_next_trading_date=AsyncMock(return_value=end))

  def forbidden(*args, **kwargs):
    pytest.fail("external certification must not open a database/calendar client")

  monkeypatch.setattr(runner, "InfrastructureResearchDataSource", forbidden)
  monkeypatch.setattr(trading_time_service, "TradingDateHelper", forbidden)
  monkeypatch.setattr(training, "RuntimeMemoryMonitor", lambda **kwargs: nullcontext())
  panel = pd.DataFrame(
    {"event_date": pd.DatetimeIndex([end]).as_unit("ns"), "stock_code": ["600000.SH"]}
  )

  async def stage(actual_source, study, directory, monitor):
    assert actual_source is source
    assert study.date_range[1] == end
    path = directory / "panel.parquet"
    panel.to_parquet(path)
    return SimpleNamespace(
      paths=[path], calendar=pd.DatetimeIndex([end]), quality={"kind": "frozen"}
    )

  monkeypatch.setattr(training, "stage_indicator_features", stage)
  if not complete:
    with pytest.raises(ValueError, match="冻结交易日历"):
      await training._source_panel(config, tmp_path, source=source)
  else:
    result, sessions, quality = await training._source_panel(
      config, tmp_path, source=source, calendar=calendar
    )
    pd.testing.assert_frame_equal(result, panel)
    assert list(sessions.date) == [end]
    assert quality == {"kind": "frozen"}
    calendar.get_next_trading_date.assert_awaited_once_with(
      "SH", config.data.date_range[1]
    )
