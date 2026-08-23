from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_infrastructure.core.data.tick_identity import tick_storage_time
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.services.t_trade_instrument_profile_service import (
  T_TRADE_PROFILE_SCHEMA_VERSION,
  T_TRADE_PROFILE_VERSION,
  TTradeInstrumentProfileService,
)


def _complete_day(day: datetime, *, code: str = "600000.SH") -> list[SimpleNamespace]:
  rows: list[SimpleNamespace] = []
  amount = 0.0
  ordinal = 0
  for session_start in (day.replace(hour=9, minute=30), day.replace(hour=13, minute=0)):
    for minute in range(120):
      at = session_start + timedelta(minutes=minute, seconds=30)
      wave = ((minute % 12) - 6) * 0.015
      price = 10.0 + day.day * 0.001 + wave
      amount += 80_000.0 + (minute % 7) * 10_000.0
      rows.append(
        SimpleNamespace(
          stock_code=code,
          time=at,
          tick_ordinal=ordinal,
          last_price=price,
          amount=amount,
          price_tick=0.01,
          bid_price=[price - 0.01],
          ask_price=[price + 0.01],
        )
      )
      ordinal += 1
  return rows


def _history(days: int = 10) -> list[SimpleNamespace]:
  start = datetime(2026, 7, 1)
  ticks: list[SimpleNamespace] = []
  for offset in range(days):
    ticks.extend(_complete_day(start + timedelta(days=offset)))
  return ticks


def _authoritative(ticks: list[SimpleNamespace]) -> list[SimpleNamespace]:
  authoritative: list[SimpleNamespace] = []
  for tick in ticks:
    row = SimpleNamespace(**vars(tick))
    row.source_time_ms = int(time_utils.to_utc(row.time).timestamp() * 1000)
    row.tick_ordinal = int(getattr(row, "tick_ordinal", 0) or 0)
    row.time = tick_storage_time(row.source_time_ms, row.tick_ordinal)
    authoritative.append(row)
  return sorted(
    authoritative,
    key=lambda row: (row.source_time_ms, row.tick_ordinal),
  )


def _pages(
  ticks: list[SimpleNamespace],
  page_size: int,
) -> list[list[SimpleNamespace]]:
  return [ticks[index : index + page_size] for index in range(0, len(ticks), page_size)]


def test_profile_build_is_deterministic_complete_and_domain_compatible() -> None:
  ticks = _history()
  cutoff = datetime(2026, 7, 10, 15, 0)
  service = TTradeInstrumentProfileService()

  first = service.build_profile(
    instrument_code="600000.sh",
    ticks=ticks,
    as_of=cutoff,
  )
  second = service.build_profile(
    instrument_code="600000.SH",
    ticks=list(reversed(ticks)),
    as_of=cutoff,
  )

  assert first.fingerprint == second.fingerprint
  assert first.schema_version == T_TRADE_PROFILE_SCHEMA_VERSION
  assert first.version.startswith(f"{T_TRADE_PROFILE_VERSION}.")
  assert first.profile["complete_trade_days"] == 10
  assert first.profile["pullback_threshold_pct"] > 0
  assert first.profile["momentum_rise_threshold_pct"] > 0
  assert first.profile["momentum_amount_velocity_ratio"] >= 1.25
  assert first.profile["pullback_max_spread_ticks"] <= 3
  assert first.profile["momentum_max_spread_ticks"] <= 10
  assert first.metrics["minute_count"] == 2_400
  assert first.metrics["minute_coverage_ratio"] == 1.0
  assert first.data_manifest["selected_trade_dates"][0] == "2026-07-01"
  assert first.data_manifest["selected_trade_dates"][-1] == "2026-07-10"


def test_profile_ignores_future_suffix_without_leaking_metadata() -> None:
  ticks = _history()
  cutoff = datetime(2026, 7, 10, 15, 0)
  service = TTradeInstrumentProfileService()
  prefix = service.build_profile(
    instrument_code="600000.SH",
    ticks=ticks,
    as_of=cutoff,
  )
  future = _complete_day(datetime(2026, 7, 11))
  with_future = service.build_profile(
    instrument_code="600000.SH",
    ticks=[*ticks, *future],
    as_of=cutoff,
  )

  assert with_future.fingerprint == prefix.fingerprint
  assert with_future.profile == prefix.profile
  assert with_future.metrics == prefix.metrics
  assert with_future.data_manifest == prefix.data_manifest
  assert datetime.fromisoformat(with_future.data_manifest["source_max_at"]) <= cutoff


def test_same_as_of_correction_gets_a_new_immutable_materialization_version() -> None:
  ticks = _history()
  cutoff = datetime(2026, 7, 10, 15, 0)
  service = TTradeInstrumentProfileService()
  first = service.build_profile(
    instrument_code="600000.SH",
    ticks=ticks,
    as_of=cutoff,
  )
  corrected = list(ticks)
  corrected[-1] = SimpleNamespace(**vars(corrected[-1]))
  corrected[-1].amount += 1_000_000.0
  second = service.build_profile(
    instrument_code="600000.SH",
    ticks=corrected,
    as_of=cutoff,
  )

  assert second.fingerprint != first.fingerprint
  assert second.version != first.version
  assert second.as_of == first.as_of


def test_profile_rejects_incomplete_history_and_invalid_day_limits() -> None:
  service = TTradeInstrumentProfileService()
  with pytest.raises(ValueError, match="完整交易日不足"):
    service.build_profile(
      instrument_code="600000.SH",
      ticks=_history(9),
      as_of=datetime(2026, 7, 10, 15, 0),
    )
  with pytest.raises(ValueError, match="minimum <= target"):
    service.build_profile(
      instrument_code="600000.SH",
      ticks=_history(),
      as_of=datetime(2026, 7, 10, 15, 0),
      target_complete_days=9,
      min_complete_days=10,
    )


@pytest.mark.asyncio
async def test_profile_save_uses_immutable_repository_contract() -> None:
  repository = SimpleNamespace(save_profile=AsyncMock(return_value="saved"))
  service = TTradeInstrumentProfileService()

  result = await service.build_and_save_profile(
    instrument_code="600000.SH",
    ticks=_history(),
    as_of=datetime(2026, 7, 10, 15, 0),
    repository=repository,
  )

  assert result == "saved"
  arguments = repository.save_profile.await_args.kwargs
  assert arguments["instrument_code"] == "600000.SH"
  assert len(arguments["fingerprint"]) == 64
  assert arguments["data_manifest"]["source_max_at"] <= (
    arguments["as_of"].isoformat(timespec="milliseconds")
  )


def test_streaming_profile_matches_full_build_across_three_pages() -> None:
  service = TTradeInstrumentProfileService()
  ticks = _history()
  full = service.build_profile(
    instrument_code="600000.SH",
    ticks=ticks,
    as_of=datetime(2026, 7, 10, 15, 0),
  )
  pages = _pages(_authoritative(ticks), 1_000)

  streamed = service.build_profile_from_pages(
    instrument_code="600000.SH",
    pages=pages,
    as_of=datetime(2026, 7, 10, 15, 0),
    lookback_calendar_days=10,
    page_size=1_000,
  )

  assert len(pages) >= 3
  assert streamed.fingerprint == full.fingerprint
  assert streamed.data_manifest["input_tick_count"] == 2_400


def test_streaming_profile_keeps_last_same_millisecond_ordinal_across_pages() -> None:
  service = TTradeInstrumentProfileService()
  ticks = _authoritative(_history())
  first = ticks[0]
  replacement = SimpleNamespace(**vars(first))
  replacement.tick_ordinal = 1
  replacement.last_price = first.last_price + 0.25
  replacement.amount = first.amount + 10.0
  replacement.time = tick_storage_time(replacement.source_time_ms, 1)
  ticks.insert(1, replacement)
  ticks.sort(key=lambda row: (row.source_time_ms, row.tick_ordinal))

  full = service.build_profile(
    instrument_code="600000.SH",
    ticks=ticks,
    as_of=datetime(2026, 7, 10, 15, 0),
  )
  streamed = service.build_profile_from_pages(
    instrument_code="600000.SH",
    pages=_pages(ticks, 1),
    as_of=datetime(2026, 7, 10, 15, 0),
    lookback_calendar_days=10,
    page_size=1,
    max_pages=3_000,
  )

  assert streamed.fingerprint == full.fingerprint
  assert streamed.data_manifest["input_tick_count"] == 2_401


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "pages",
  [
    "repeated",
    "non_progressing",
  ],
)
async def test_streaming_integrity_failure_never_saves_profile(pages: str) -> None:
  service = TTradeInstrumentProfileService()
  source = _authoritative(_history())
  first = source[:10]
  if pages == "repeated":
    page_stream = [first, first]
  else:
    page_stream = [first, [source[9], source[8]]]

  async def stream():
    for page in page_stream:
      yield page

  repository = SimpleNamespace(save_profile=AsyncMock(return_value="saved"))
  with pytest.raises(ValueError, match="未保存画像"):
    await service.build_and_save_profile_from_pages(
      instrument_code="600000.SH",
      pages=stream(),
      as_of=datetime(2026, 7, 10, 15, 0),
      repository=repository,
      lookback_calendar_days=10,
    )
  repository.save_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_streaming_page_limit_fails_closed_without_save() -> None:
  service = TTradeInstrumentProfileService()
  source = _authoritative(_history())

  async def stream():
    yield source[:10]
    yield source[10:20]

  repository = SimpleNamespace(save_profile=AsyncMock(return_value="saved"))
  with pytest.raises(ValueError, match="页数超过安全上限"):
    await service.build_and_save_profile_from_pages(
      instrument_code="600000.SH",
      pages=stream(),
      as_of=datetime(2026, 7, 10, 15, 0),
      repository=repository,
      lookback_calendar_days=10,
      max_pages=1,
    )
  repository.save_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_streaming_source_tick_limit_fails_closed_without_save() -> None:
  service = TTradeInstrumentProfileService()
  source = _authoritative(_history())

  async def stream():
    yield source[:10]

  repository = SimpleNamespace(save_profile=AsyncMock(return_value="saved"))
  with pytest.raises(ValueError, match="总量超过安全上限"):
    await service.build_and_save_profile_from_pages(
      instrument_code="600000.SH",
      pages=stream(),
      as_of=datetime(2026, 7, 10, 15, 0),
      repository=repository,
      lookback_calendar_days=10,
      max_source_ticks=5,
    )
  repository.save_profile.assert_not_awaited()


def test_streaming_minute_slot_bound_is_hard_and_causal_cutoff_is_ignored() -> None:
  service = TTradeInstrumentProfileService()
  source = _authoritative(_history())
  source.extend(_authoritative(_complete_day(datetime(2026, 7, 11)))[:1])
  source.sort(key=lambda row: (row.source_time_ms, row.tick_ordinal))

  with pytest.raises(ValueError, match="内存上限"):
    service.build_profile(
      instrument_code="600000.SH",
      ticks=source[:2],
      as_of=datetime(2026, 7, 10, 15, 0),
      max_minute_entries=1,
    )

  future = SimpleNamespace(**vars(source[-1]))
  future.source_time_ms = int(
    time_utils.to_utc(datetime(2026, 7, 11, 14, 59, 30)).timestamp() * 1000
  )
  future.tick_ordinal = 0
  future.time = tick_storage_time(future.source_time_ms, future.tick_ordinal)
  prefix = service.build_profile_from_pages(
    instrument_code="600000.SH",
    pages=_pages(source[:2_400], 1_000),
    as_of=datetime(2026, 7, 10, 15, 0),
    lookback_calendar_days=10,
  )
  with_future = service.build_profile_from_pages(
    instrument_code="600000.SH",
    pages=_pages([*source[:2_400], future], 1_000),
    as_of=datetime(2026, 7, 10, 15, 0),
    lookback_calendar_days=10,
  )
  assert with_future.fingerprint == prefix.fingerprint

  mismatched_future = SimpleNamespace(**vars(future))
  mismatched_future.time = datetime(2026, 7, 10, 14, 59, 30)
  with pytest.raises(ValueError, match="存储时间与源身份不一致"):
    service.build_profile_from_pages(
      instrument_code="600000.SH",
      pages=_pages([*source[:2_400], mismatched_future], 1_000),
      as_of=datetime(2026, 7, 10, 15, 0),
      lookback_calendar_days=10,
    )
