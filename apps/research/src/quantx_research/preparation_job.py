"""Isolated data coverage/certification/GPU commands for durable preparation jobs."""

import asyncio
import hashlib
import json
import os
import sys
import threading
import time
from datetime import date
from pathlib import Path

import pandas as pd
import psutil
import yaml
from quantx_contracts.research_preparation import ResearchPreparationConfig
from quantx_infrastructure.services.research_preparation import (
  download_preview,
  evidence_file,
  reject_links,
  require_development_export,
  root,
)
from quantx_infrastructure.services.trading_time_service import TradingDateHelper

from quantx_research.data.factor_coverage import build_dividend_factor_coverage_report
from quantx_research.data.source import InfrastructureResearchDataSource
from quantx_research.next_day_selection_config import load_next_day_selection_config
from quantx_research.next_day_selection_training import _load_history

HISTORIES = (
  ("st_file", "is_st", "历史 ST"),
  ("industry_file", "industry", "历史行业"),
  ("delisting_file", "delisting_risk", "历史退市"),
)


def file_hash(path):
  with path.open("rb") as stream:
    return hashlib.file_digest(stream, "sha256").hexdigest()


async def coverage(config, *, source=None, calendar=None):
  calendar = calendar or TradingDateHelper()
  preview = await download_preview(config, calendar)
  checks, histories, hashes = [], {}, {}

  def check(name, ok, detail, missing=False):
    checks.append(
      {
        "name": name,
        "status": "READY" if ok else ("MISSING" if missing else "BLOCKED"),
        "detail": detail,
      }
    )

  for key, column, label in HISTORIES:
    reference = getattr(config, key)
    try:
      if not reference:
        raise ValueError("尚未选择历史证据文件")
      path = evidence_file(reference)
      before = file_hash(path)
      frame = _load_history(path, column)
      if frame.empty or frame[column].isna().any() or frame["event_date"].isna().any():
        raise ValueError("历史证据存在空值或无效日期")
      if column != "industry" and not frame[column].isin([True, False, 0, 1]).all():
        raise ValueError("历史状态必须是布尔值或 0/1")
      if file_hash(path) != before:
        raise ValueError("读取期间历史文件发生变化")
      histories[key] = frame
      hashes[key] = before
    except (ValueError, OSError):
      check(
        label,
        False,
        "未配置、字段无效或文件已变化；需 event_date、stock_code 及对应历史值",
        missing=True,
      )
  if source is None:
    async with InfrastructureResearchDataSource() as actual:
      return await _coverage_source(
        config, actual, calendar, preview, checks, histories, hashes
      )
  return await _coverage_source(
    config, source, calendar, preview, checks, histories, hashes
  )


async def _coverage_source(
  config, source, calendar, preview, checks, histories, hashes
):
  start, end = date.fromisoformat(preview["start"]), date.fromisoformat(preview["end"])
  requested = set(config.stock_codes)
  if not requested and "st_file" in histories:
    history = histories["st_file"]
    requested = set(
      history.loc[
        history.event_date.between(
          pd.Timestamp(config.date_start), pd.Timestamp(config.date_end)
        ),
        "stock_code",
      ]
    )
  instruments = await source.list_instruments(codes=sorted(requested) or None)
  codes = sorted(
    code
    for code in instruments.stock_code.unique()
    if __import__("re").fullmatch(
      r"(?:60\d{4}|68\d{4})\.SH|(?:00\d{4}|30\d{4})\.SZ", code
    )
  )
  metadata_ok = bool(codes) and (not requested or requested <= set(codes))
  checks.append(
    {
      "name": "股票范围",
      "status": "READY" if metadata_ok else "BLOCKED",
      "detail": f"{len(codes)} 只已落地证券；范围取自明确代码或历史 ST 文件，当前证券列表不能证明历史全市场完整",
    }
  )
  dates = await calendar.get_trading_calendar("SH", start, min(end, date.today()))
  expected_dates = pd.DatetimeIndex(dates)
  benchmark = await source.load_daily_bars(
    [config.benchmark_code], start, min(end, date.today())
  )
  benchmark_dates = pd.DatetimeIndex(pd.to_datetime(benchmark.time).dropna().unique())
  benchmark_ok = (
    bool(len(expected_dates)) and len(expected_dates.difference(benchmark_dates)) == 0
  )
  checks.extend(
    [
      {
        "name": "交易日历",
        "status": "READY" if len(dates) else "MISSING",
        "detail": f"运行端交易日历：{len(dates)} 个交易日",
      },
      {
        "name": "基准日线",
        "status": "READY" if benchmark_ok else "MISSING",
        "detail": f"缺少 {len(expected_dates.difference(benchmark_dates))} 个交易日",
      },
      {
        "name": "次日标签",
        "status": "READY" if preview["label_available"] else "BLOCKED",
        "detail": f"需覆盖至 {preview['end']}；未来标签不补造",
      },
    ]
  )
  missing, warmup_short, rows, analysis_rows = 0, 0, 0, 0
  history_missing = {key: 0 for key in histories}
  history_indexes = {
    key: pd.MultiIndex.from_frame(frame[["event_date", "stock_code"]])
    for key, frame in histories.items()
  }
  for offset in range(0, len(codes), 100):
    batch = codes[offset : offset + 100]
    bars = await source.load_daily_bars(
      batch, start, min(end, date.today()), batch_size=100
    )
    bars["event_date"] = pd.to_datetime(bars.time).dt.normalize()
    rows += len(bars)
    for code in batch:
      values = bars.loc[bars.stock_code == code]
      info = instruments.loc[instruments.stock_code == code].iloc[0]
      opened = pd.to_datetime(info.get("open_date"), errors="coerce")
      needed = (
        expected_dates if pd.isna(opened) else expected_dates[expected_dates >= opened]
      )
      expired = pd.to_datetime(info.get("expire_date"), errors="coerce")
      if not pd.isna(expired):
        needed = needed[needed <= expired]
      missing += len(needed.difference(pd.DatetimeIndex(values.event_date)))
      if (
        sum(values.event_date <= pd.Timestamp(config.date_end))
        < config.minimum_listing_days
      ):
        warmup_short += 1
    selected = bars.loc[
      bars.event_date.between(
        pd.Timestamp(config.date_start), pd.Timestamp(config.date_end)
      ),
      ["event_date", "stock_code"],
    ]
    keys = pd.MultiIndex.from_frame(selected)
    analysis_rows += len(selected)
    for key, index in history_indexes.items():
      history_missing[key] += int((index.get_indexer(keys) < 0).sum())
  checks.append(
    {
      "name": "股票日线",
      "status": "READY" if rows and missing == 0 else "MISSING",
      "detail": f"读取 {rows} 行；缺少 {missing} 个证券交易日（含需核验的停牌/退市区间）",
    }
  )
  checks.append(
    {
      "name": "指标预热",
      "status": "READY" if rows and warmup_short < len(codes) else "BLOCKED",
      "detail": f"{warmup_short} 只股票历史不足，将在认证时排除；其他标的仍需经过逐日因子完整度及最低上市天数检查",
    }
  )
  checks.append(
    {
      "name": "训练区间候选行",
      "status": "READY" if analysis_rows else "MISSING",
      "detail": f"{analysis_rows} 行；最终样本还需通过上市天数、指标与标签检查",
    }
  )
  factor_evidence = await source.load_dividend_factor_coverage(
    codes, start=start, end=min(end, date.today())
  )
  factors = build_dividend_factor_coverage_report(
    factor_evidence,
    requested_codes=codes,
    requested_start=start,
    requested_end=min(end, date.today()),
  )
  checks.append(
    {
      "name": "指标复权依赖",
      "status": "READY" if factors.is_complete else "MISSING",
      "detail": f"{len(factors.covered_codes)}/{len(codes)} 只股票具有可核验的复权覆盖；下载任务将补齐",
    }
  )
  for key, _, label in HISTORIES:
    if key in histories:
      count = history_missing[key]
      checks.append(
        {
          "name": label,
          "status": "READY" if rows and count == 0 else "MISSING",
          "detail": f"缺少 {count} 个已落地样本的历史值；SHA-256 {hashes[key]}",
        }
      )
  if not config.stock_codes and not requested:
    checks.append(
      {
        "name": "历史股票池",
        "status": "BLOCKED",
        "detail": "全市场范围需要历史证据文件；当前股票列表仅可用于下载准备",
      }
    )
  return {
    "checks": checks,
    "ready": all(item["status"] == "READY" for item in checks),
    "preview": preview,
    "downloadable": metadata_ok,
    "file_hashes": hashes,
    "download": {
      "stock_list": sorted(set(codes) | {config.benchmark_code}),
      "periods": ["1d"],
      "start_time": start.strftime("%Y%m%d"),
      "end_time": min(end, date.today()).strftime("%Y%m%d"),
      "compute_daily_signals": False,
    },
    "stock_codes": codes,
  }


async def execute(request, directory):
  kind = request["kind"]
  if kind == "CERTIFY_FROZEN":
    from quantx_contracts.research_preparation import CertificationInputReference

    from quantx_research.certification_inputs import certify_frozen_inputs
    from quantx_research.next_day_selection_dataset import (
      load_certified_dataset_manifest,
    )

    reference = CertificationInputReference.model_validate(request["certification_input"])
    version = request["dataset_version"]
    if reference.bundle.source_id != version:
      raise ValueError("Certification input identity mismatch")
    inputs, output_root = Path(request["input_directory"]), Path(request["output_root"])
    for path in (inputs, output_root):
      if not path.is_absolute():
        raise ValueError("Frozen certification requires absolute local paths")
      reject_links(path)
    output = await certify_frozen_inputs(
      inputs, dataset_version=version, manifest_sha256=reference.manifest_sha256,
      work_directory=directory, output_root=output_root,
    )
    manifest = load_certified_dataset_manifest(output)
    return {
      "ready": True, "dataset_version": manifest["dataset_version"],
      "manifest_sha256": manifest["manifest_sha256"],
      "sample_count": manifest["quality"]["sample_count"],
      "input_manifest_sha256": reference.manifest_sha256,
    }
  config = ResearchPreparationConfig.model_validate(request["config"])
  if kind in {"COVERAGE", "DOWNLOAD"}:
    return await coverage(config)
  if kind == "GPU":
    from quantx_research import next_day_selection_gpu as gpu
    from quantx_research.next_day_selection_gpu import qualify_lightgbm_gpu

    build = Path(request["build_evidence"])
    dataset = Path(request["dataset_directory"])
    output = Path(request["qualification_output"])
    for local_path in (build, dataset, output):
      if not local_path.is_absolute():
        raise ValueError("Trainer GPU input paths must be absolute")
      reject_links(local_path)
    if not build.is_file():
      return {
        "ready": False,
        "error": "Trainer 隔离状态目录缺少官方 GPU wheel，请按 GPU 部署文档安装证据文件",
      }
    result = qualify_lightgbm_gpu(
      dataset, build_evidence=build, output=output,
    )
    checks = []
    for label, key in [
      ("CPU 重载", "model_cpu_loadable"),
      ("显存采样", "memory_sampling_available"),
      ("重复性", "repeat_consistent"),
      ("有限数值", "no_non_finite"),
      ("结论一致", "conclusion_not_flipped"),
    ]:
      checks.append(
        {
          "name": label,
          "status": "READY" if result[key] else "BLOCKED",
          "detail": str(result[key]),
        }
      )
    for label, key, threshold, minimum in [
      (
        "FP32 Brier 差异",
        "brier_relative_difference",
        gpu.GPU_BRIER_RELATIVE_TOLERANCE,
        False,
      ),
      (
        "FP64 Brier 差异",
        "fp64_brier_relative_difference",
        gpu.GPU_BRIER_RELATIVE_TOLERANCE,
        False,
      ),
      (
        "FP32 ECE 差异",
        "ece_absolute_difference",
        gpu.GPU_ECE_ABSOLUTE_TOLERANCE,
        False,
      ),
      (
        "FP64 ECE 差异",
        "fp64_ece_absolute_difference",
        gpu.GPU_ECE_ABSOLUTE_TOLERANCE,
        False,
      ),
      ("FP32 排名重叠", "top20_overlap", gpu.GPU_TOP20_OVERLAP_MINIMUM, True),
      ("FP64 排名重叠", "fp64_top20_overlap", gpu.GPU_TOP20_OVERLAP_MINIMUM, True),
      ("加速比例", "speedup", gpu.GPU_MIN_SPEEDUP, True),
      (
        "峰值显存比例",
        "peak_memory_fraction",
        gpu.DEFAULT_GPU_MAX_MEMORY_FRACTION,
        False,
      ),
      ("CPU 重载精度", "cpu_reload_max_abs_difference", 1e-12, False),
      ("FP64 CPU 重载精度", "fp64_cpu_reload_max_abs_difference", 1e-12, False),
    ]:
      value = result[key]
      passed = isinstance(value, (int, float)) and (
        value >= threshold if minimum else value <= threshold
      )
      checks.append(
        {
          "name": label,
          "status": "READY" if passed else "BLOCKED",
          "detail": f"{value}；要求 {'>=' if minimum else '<='} {threshold}",
        }
      )
    return {
      "ready": result["status"] == "GPU_AVAILABLE",
      "status": result["status"],
      "evidence_sha256": result["evidence_sha256"],
      "speedup": result["speedup"],
      "checks": [
        {
          "name": "GPU 资格",
          "status": result["status"],
          "detail": "资格指标已保存；未通过门禁时继续使用 CPU",
        }
      ]
      + checks,
    }
  if kind != "CERTIFY":
    raise ValueError("Unknown preparation job kind")
  require_development_export()
  from quantx_research.certification_inputs import (
    certification_input_reference,
    export_certification_inputs,
  )

  inputs = directory / "certification-inputs"
  reject_links(inputs)
  if inputs.exists():
    digest = file_hash(inputs / "manifest.json")
    reference = certification_input_reference(inputs, dataset_version=request["dataset_version"], manifest_sha256=digest)
    return {"ready": True, "dataset_version": request["dataset_version"], "certification_input": reference.model_dump(mode="json"), "checks": []}
  report = await coverage(config)
  if not report["ready"]:
    return report
  template = load_next_day_selection_config(
    root() / "apps/research/configs/next_day_selection_v1.yaml"
  ).model_dump(mode="json")
  data = template["data"]
  data.update(
    date_range=[config.date_start.isoformat(), config.date_end.isoformat()],
    stock_codes=report["stock_codes"],
    universe_kind="EXPLICIT",
    index_code=None,
    benchmark_code=config.benchmark_code,
    minimum_listing_days=config.minimum_listing_days,
    market_data_archive=None,
    verified_panel_path=None,
  )
  for (key, _, _), target in zip(
    HISTORIES,
    [
      "historical_st_membership_path",
      "historical_industry_membership_path",
      "historical_delisting_status_path",
    ],
  ):
    path = evidence_file(getattr(config, key))
    if file_hash(path) != report["file_hashes"][key]:
      raise ValueError("历史证据已变化，请重新检查")
    # Freeze the exact validated bytes for this job; user edits cannot race certification.
    frozen = directory / f"{target}{path.suffix}"
    reject_links(frozen)
    frozen.write_bytes(path.read_bytes())
    if file_hash(frozen) != report["file_hashes"][key]:
      raise ValueError("历史证据复制期间发生变化")
    data[target] = str(frozen)
  config_path = directory / "config.yaml"
  reject_links(config_path)
  config_path.write_text(yaml.safe_dump(template, allow_unicode=True), encoding="utf-8")
  async with InfrastructureResearchDataSource() as source:
    digest = await export_certification_inputs(
      config_path, source, TradingDateHelper(), inputs,
      dataset_version=request["dataset_version"],
    )
  reference = certification_input_reference(inputs, dataset_version=request["dataset_version"], manifest_sha256=digest)
  return {
    "ready": True, "dataset_version": request["dataset_version"],
    "certification_input": reference.model_dump(mode="json"), "checks": report["checks"],
  }


def main():
  from quantx_infrastructure.training_host_guard import (
    HostAdmissionDenied,
    high_resource_guard,
  )

  try:
    with high_resource_guard():
      _execute_main()
  except HostAdmissionDenied as exc:
    print(f"主机训练门禁: {exc}", flush=True)
    raise SystemExit(75) from None


def _execute_main():
  parent = psutil.Process(os.getppid())

  def watch_parent():
    while True:
      time.sleep(5)
      if not parent.is_running():
        os._exit(3)

  threading.Thread(target=watch_parent, daemon=True).start()
  path = Path(sys.argv[1]).absolute()
  reject_links(path)
  directory = path.parent
  try:
    result = asyncio.run(
      execute(json.loads(path.read_text(encoding="utf-8")), directory)
    )
  except Exception as exc:
    # Never propagate database DSNs or host paths from dependency exceptions.
    result = {
      "ready": False,
      "error": f"准备任务失败（{type(exc).__name__}）；请检查数据覆盖、运行端配置和依赖",
    }
  reject_links(directory / "result.json")
  (directory / "result.json").write_text(
    json.dumps(result, ensure_ascii=False), encoding="utf-8"
  )


if __name__ == "__main__":
  main()
