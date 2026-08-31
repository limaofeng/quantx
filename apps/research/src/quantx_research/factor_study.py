"""Calendar-aligned factor cohorts and bounded-memory descriptive inference.

Only one market month is loaded at a time. Exact pooled medians are backed by
temporary numeric files, not a full-market pandas panel. Inference is on paired
daily differences, never on independent stock-day Bernoulli trials.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from quantx_domain.factors import (
  FACTOR_DEFINITIONS,
  FACTOR_VERSION,
)
from quantx_domain.factors import (
  condition_mask as shared_condition_mask,
)

from quantx_research.core.statistics import DateBlockBootstrap
from quantx_research.factor_config import FactorStudyConfig
from quantx_research.runtime_memory import RuntimeMemoryMonitor

WARNINGS = [
  "结果是历史条件关联，不是个股上涨概率预测、因果结论或交易收益。",
  "当前证券主表不能还原历史成分、ST、行业及完整退市总体，存在生存者偏差。",
  "未模拟 T+1、涨跌停可成交性、停牌退出、手续费与滑点。",
  "最近一年仅作稳定性检查，已经观察过的结果不属于独立样本外验证。",
  "连续因子按每日截面五分位分组；相同值不拆分。分组取值范围是跨日期观测范围，不是固定筛选阈值。",
  "上涨比例提升及均值差异按同日配对、日期等权计算；不同单因子分组的可观察日期可能不同。",
]


def report_match_key(
  kind: str,
  factor_ids: list[str],
  conditions: list[dict[str, object]],
  universe: dict[str, object],
) -> str:
  payload = {
    "kind": kind,
    "factor_version": FACTOR_VERSION,
    "factor_ids": sorted(factor_ids),
    "conditions": conditions,
    "universe": universe,
  }
  return hashlib.sha256(
    json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      ensure_ascii=False,
    ).encode()
  ).hexdigest()


def factor_groups(values: pd.Series, *, binary: bool) -> pd.Series:
  """Daily five-quantile groups with deterministic, unsplit equal values."""
  result = pd.Series(pd.NA, index=values.index, dtype="string")
  usable = pd.to_numeric(values, errors="coerce")
  usable = usable[np.isfinite(usable)]
  if usable.empty:
    return result
  if binary:
    result.loc[usable.index] = np.where(usable != 0, "true", "false")
  else:
    # The midrank assigns equal observations to the same bin. A constant
    # cross section therefore occupies Q3, not five fabricated subgroups.
    percentile = (usable.rank(method="average") - 0.5) / len(usable)
    bins = np.minimum(np.floor(percentile.to_numpy() * 5).astype(int), 4) + 1
    result.loc[usable.index] = [f"Q{value}" for value in bins]
  return result


def condition_mask(frame: pd.DataFrame, condition: dict[str, object]) -> pd.Series:
  return shared_condition_mask(frame, [condition])


def add_factor_outcomes(
  frame: pd.DataFrame,
  calendar: pd.DatetimeIndex,
  horizons: tuple[int, ...],
) -> pd.DataFrame:
  """Price responses use exact market dates, independently at every horizon."""
  result = frame.copy()
  for _, indices in result.groupby("stock_code", sort=False).groups.items():
    stock = result.loc[indices]
    dense = stock.set_index("event_date").reindex(calendar)
    close = dense["close"].where(dense["outcome_valid"].eq(True))
    entry = dense["open"].where(dense["outcome_valid"].eq(True)).shift(-1)
    for horizon in horizons:
      target = close.shift(-horizon)
      for basis, denominator in (("close", close), ("next_open", entry)):
        value = target.div(denominator.where(denominator > 0)).sub(1.0)
        result.loc[indices, f"{basis}_return_h{horizon}"] = value.reindex(
          pd.DatetimeIndex(stock["event_date"])
        ).to_numpy()
  return result


@dataclass
class _Cell:
  path: Path
  days: list[dict[str, Any]] = field(default_factory=list)
  stock_codes: dict[int, set[str]] = field(default_factory=dict)
  last_seen: dict[str, pd.Timestamp] = field(default_factory=dict)
  count: int = 0
  pending: list[np.ndarray] = field(default_factory=list)

  def append(self, day: pd.Timestamp, values: np.ndarray, codes: np.ndarray) -> None:
    if not len(values):
      return
    self.pending.append(np.asarray(values, dtype="float64"))
    self.days.append(
      {
        "event_date": day,
        "count": len(values),
        "sum": float(values.sum()),
        "wins": int((values > 0).sum()),
        "offset": self.count,
      }
    )
    self.stock_codes.setdefault(day.year, set()).update(codes)
    self.last_seen.update(dict.fromkeys(codes, day))
    self.count += len(values)

  def flush(self) -> None:
    if self.pending:
      with self.path.open("ab") as stream:
        for values in self.pending:
          stream.write(values.tobytes())
      self.pending.clear()


def analyze_factor_partitions(
  month_partitions: dict[str, list[Path]],
  config: FactorStudyConfig,
  *,
  staging_directory: Path,
  monitor: RuntimeMemoryMonitor,
  data_start: str,
  data_end: str,
  calendar: pd.DatetimeIndex | None = None,
) -> dict[str, Any]:
  definitions = {item.id: item for item in FACTOR_DEFINITIONS}
  reports: list[dict[str, Any]] = []
  requested = [("single", [factor_id], []) for factor_id in config.factor_ids]
  if config.conditions:
    requested.append(
      (
        "joint",
        sorted({str(item["factor_id"]) for item in config.conditions}),
        list(config.conditions),
      )
    )
  for index, (kind, factor_ids, conditions) in enumerate(requested, start=1):
    monitor.checkpoint("factor_report_start")
    print(
      f"factor-study report {index}/{len(requested)}: {kind} {','.join(factor_ids)}",
      flush=True,
    )
    with tempfile.TemporaryDirectory(
      prefix="factor-statistics-", dir=staging_directory
    ) as scratch:
      report = _analyze_report(
        month_partitions,
        config,
        kind=kind,
        factor_ids=factor_ids,
        conditions=conditions,
        scratch=Path(scratch),
        monitor=monitor,
        calendar=calendar,
      )
    report["definitions"] = [asdict(definitions[value]) for value in factor_ids]
    reports.append(report)
  # One preregistered family per endpoint includes every requested report,
  # cohort, horizon and return basis. Annual checks are descriptive only.
  for p_field, q_field in (("p_value", "q_value"), ("mean_p_value", "mean_q_value")):
    rows = [
      row
      for report in reports
      for row in report["rows"]
      if row["period"] == "all" and row[p_field] is not None
    ]
    ordered = sorted(rows, key=lambda row: row[p_field])
    corrected = 1.0
    for rank in range(len(ordered), 0, -1):
      row = ordered[rank - 1]
      corrected = min(corrected, row[p_field] * len(ordered) / rank)
      row[q_field] = corrected
  return {
    "schema_version": 1,
    "study_id": config.study_id,
    "factor_version": FACTOR_VERSION,
    "data_start": data_start,
    "data_end": data_end,
    "universe": config.universe.identity(),
    "horizons": list(config.outcomes.horizons),
    "return_bases": ["close", "next_open"],
    "reports": reports,
    "warnings": WARNINGS,
  }


def _analyze_report(
  partitions: dict[str, list[Path]],
  config: FactorStudyConfig,
  *,
  kind: str,
  factor_ids: list[str],
  conditions: list[dict[str, object]],
  scratch: Path,
  monitor: RuntimeMemoryMonitor,
  calendar: pd.DatetimeIndex | None = None,
) -> dict[str, Any]:
  binary = {item.id: item.kind == "binary" for item in FACTOR_DEFINITIONS}
  groups = (
    (
      ["baseline", "false", "true"]
      if binary[factor_ids[0]]
      else ["baseline", "Q1", "Q2", "Q3", "Q4", "Q5"]
    )
    if kind == "single"
    else (
      [
        "baseline",
        *[f"condition_{index + 1}" for index in range(len(conditions))],
        "joint",
      ]
    )
  )
  cells = {
    (group, horizon, basis): _Cell(scratch / f"{group}-{horizon}-{basis}.bin")
    for group in groups
    for horizon in config.outcomes.horizons
    for basis in ("close", "next_open")
  }
  distribution: dict[str, dict[str, Any]] = {}
  sample_count = valid_count = 0
  all_codes: set[str] = set()
  all_dates: list[pd.Timestamp] = []
  columns = [
    "stock_code",
    "event_date",
    *factor_ids,
    *[
      f"{basis}_return_h{horizon}"
      for horizon in config.outcomes.horizons
      for basis in ("close", "next_open")
    ],
  ]
  for month in sorted(partitions):
    # A month-sized numeric projection is bounded independently of history
    # length. Guard the known row count before Arrow/Pandas materialization.
    paths = partitions[month]
    row_count = sum(pq.read_metadata(path).num_rows for path in paths)
    monitor.guard(
      "factor_month_projection", estimated_increment_bytes=row_count * len(columns) * 32
    )
    frame = pd.concat(
      [pd.read_parquet(path, columns=columns) for path in paths], ignore_index=True
    )
    for day, daily in frame.groupby("event_date", sort=True):
      day = pd.Timestamp(day)
      all_dates.append(day)
      sample_count += len(daily)
      valid = np.isfinite(daily[factor_ids].to_numpy(dtype=float)).all(axis=1)
      daily = daily.loc[valid].copy()
      valid_count += len(daily)
      all_codes.update(daily["stock_code"].astype(str))
      memberships = {"baseline": pd.Series(True, index=daily.index)}
      if kind == "single":
        labels = factor_groups(daily[factor_ids[0]], binary=binary[factor_ids[0]])
        for group in groups[1:]:
          memberships[group] = labels.eq(group).fillna(False)
          values = daily.loc[memberships[group], factor_ids[0]]
          if values.empty:
            continue
          item = distribution.setdefault(
            group,
            {
              "factor_id": factor_ids[0],
              "group": group,
              "lower": float(values.min()),
              "upper": float(values.max()),
              "count": 0,
              "missing_count": 0,
            },
          )
          item["count"] += len(values)
          item["lower"] = min(item["lower"], float(values.min()))
          item["upper"] = max(item["upper"], float(values.max()))
      else:
        for index, condition in enumerate(conditions):
          memberships[f"condition_{index + 1}"] = condition_mask(daily, condition)
        memberships["joint"] = pd.concat(list(memberships.values()), axis=1).all(axis=1)
      specs = [
        (horizon, basis)
        for horizon in config.outcomes.horizons
        for basis in ("close", "next_open")
      ]
      outcomes = daily[
        [f"{basis}_return_h{horizon}" for horizon, basis in specs]
      ].to_numpy(dtype=float)
      codes = daily["stock_code"].to_numpy()
      # Joint comparisons use only dates on which the joint has an observable
      # outcome; the same calendar support applies to each component/baseline.
      joint_dates = (
        np.isfinite(outcomes[memberships["joint"].to_numpy(dtype=bool)]).any(axis=0)
        if kind == "joint"
        else np.ones(len(specs), dtype=bool)
      )
      for group, membership in memberships.items():
        selected = membership.to_numpy(dtype=bool)
        group_values, group_codes = outcomes[selected], codes[selected]
        for column, (horizon, basis) in enumerate(specs):
          if not joint_dates[column]:
            continue
          values = group_values[:, column]
          eligible = np.isfinite(values)
          cells[group, horizon, basis].append(
            day, values[eligible], group_codes[eligible]
          )
      monitor.checkpoint("factor_daily_statistics")
    for cell in cells.values():
      cell.flush()
    del frame
  rows: list[dict[str, Any]] = []
  bootstrap = DateBlockBootstrap(
    pd.Series(calendar if calendar is not None else all_dates),
    samples=config.statistics.bootstrap_samples,
    seed=config.statistics.random_seed,
    confidence_level=config.statistics.confidence_level,
  )
  latest_date = max(all_dates, default=None)
  periods = ["all", *[str(year) for year in sorted({day.year for day in all_dates})]]
  if latest_date is not None:
    periods.append("latest_year")
  for (group, horizon, basis), cell in cells.items():
    monitor.guard("factor_exact_median", estimated_increment_bytes=cell.count * 24)
    values = (
      np.fromfile(cell.path, dtype="float64") if cell.path.exists() else np.array([])
    )
    for period in periods:
      rows.append(
        _cell_result(
          cell,
          cells["baseline", horizon, basis],
          values,
          group=group,
          horizon=horizon,
          basis=basis,
          period=period,
          latest_date=latest_date,
          config=config,
          bootstrap=bootstrap,
        )
      )
    del values
  warnings = list(WARNINGS)
  if config.universe.stock_codes is not None or config.universe.minimum_listing_days:
    warnings.append(
      "本运行限定股票列表或上市天数，仅供默认股票池参考，不能作为全市场条件精确匹配。"
    )
  if not valid_count:
    warnings.append("没有具备全部所需因子值的有效样本。")
  if kind == "joint" and not any(
    row["sample_count"] for row in rows if row["group"] == "joint"
  ):
    warnings.append(
      "当前条件交集没有可观察收益的样本；不能据此估计上涨比例或推断有效性。"
    )
  for item in distribution.values():
    item["missing_count"] = sample_count - valid_count
  match_key = report_match_key(kind, factor_ids, conditions, config.universe.identity())
  return {
    "report_id": f"{kind}-{factor_ids[0]}"
    if kind == "single"
    else f"joint-{match_key[:16]}",
    "kind": kind,
    "factor_ids": factor_ids,
    "conditions": conditions,
    "match_key": match_key,
    "coverage": {
      "sample_count": sample_count,
      "valid_count": valid_count,
      "missing_count": sample_count - valid_count,
      "stock_count": len(all_codes),
      "date_count": len(set(all_dates)),
      "restricted_universe": config.universe.stock_codes is not None
      or config.universe.minimum_listing_days > 0,
    },
    "distribution": list(distribution.values()),
    "rows": rows,
    "warnings": warnings,
  }


def _cell_result(
  cell: _Cell,
  baseline: _Cell,
  values: np.ndarray,
  *,
  group: str,
  horizon: int,
  basis: str,
  period: str,
  latest_date: pd.Timestamp | None,
  config: FactorStudyConfig,
  bootstrap: DateBlockBootstrap,
) -> dict[str, Any]:
  year = int(period) if period not in ("all", "latest_year") else None
  cutoff = (
    latest_date - pd.DateOffset(years=1)
    if period == "latest_year" and latest_date is not None
    else None
  )
  days = [
    day
    for day in cell.days
    if (year is None or day["event_date"].year == year)
    and (cutoff is None or day["event_date"] > cutoff)
  ]
  base_days = {day["event_date"]: day for day in baseline.days}
  n = sum(day["count"] for day in days)
  codes = (
    {code for code, last_seen in cell.last_seen.items() if last_seen > cutoff}
    if cutoff is not None
    else set().union(
      *[
        codes
        for item_year, codes in cell.stock_codes.items()
        if year is None or item_year == year
      ]
    )
  )
  if days:
    selected = values[days[0]["offset"] : days[-1]["offset"] + days[-1]["count"]]
    daily_up = np.array([day["wins"] / day["count"] for day in days])
    daily_mean = np.array([day["sum"] / day["count"] for day in days])
    base_up = np.array(
      [
        base_days[day["event_date"]]["wins"] / base_days[day["event_date"]]["count"]
        for day in days
      ]
    )
    base_mean = np.array(
      [
        base_days[day["event_date"]]["sum"] / base_days[day["event_date"]]["count"]
        for day in days
      ]
    )
  else:
    selected = daily_up = daily_mean = base_up = base_mean = np.array([])
  sufficient = (
    n >= config.statistics.minimum_cell_samples
    and len(days) >= config.statistics.minimum_inference_dates
  )
  inference_status = (
    "descriptive_only"
    if period != "all" or group == "baseline"
    else "available"
    if sufficient
    else "insufficient_sample"
  )
  ci = mean_ci = (None, None, None)
  if inference_status == "available":
    paired = pd.DataFrame(
      {
        "event_date": [day["event_date"] for day in days],
        "up_lift": daily_up - base_up,
        "mean_lift": daily_mean - base_mean,
      }
    )
    kwargs = {
      "block_length": config.statistics.block_length(horizon),
      "minimum_dates": config.statistics.minimum_inference_dates,
    }
    ci = bootstrap.infer(paired, "up_lift", **kwargs)
    mean_ci = bootstrap.infer(paired, "mean_lift", **kwargs)
    if ci[0] is None:
      inference_status = "insufficient_sample"
  return {
    "group": group,
    "horizon": horizon,
    "return_basis": basis,
    "period": period,
    "sample_count": n,
    "stock_count": len(codes),
    "date_count": len(days),
    "up_rate": sum(day["wins"] for day in days) / n if n else None,
    "mean_return": sum(day["sum"] for day in days) / n if n else None,
    "median_return": float(np.median(selected)) if n else None,
    "date_equal_up_rate": float(daily_up.mean()) if n else None,
    "date_equal_mean_return": float(daily_mean.mean()) if n else None,
    "baseline_up_rate": float(base_up.mean()) if n else None,
    "up_rate_lift": float((daily_up - base_up).mean()) if n else None,
    "mean_return_lift": float((daily_mean - base_mean).mean()) if n else None,
    "ci_low": ci[0],
    "ci_high": ci[1],
    "p_value": ci[2],
    "q_value": None,
    "mean_ci_low": mean_ci[0],
    "mean_ci_high": mean_ci[1],
    "mean_p_value": mean_ci[2],
    "mean_q_value": None,
    "inference_status": inference_status,
  }
