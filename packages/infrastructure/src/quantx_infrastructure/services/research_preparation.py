"""Preparation request persistence and safe host-side evidence references."""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from quantx_contracts.research_preparation import ResearchPreparationConfig
from sqlalchemy import select, text, update

from quantx_infrastructure.models.research_preparation import (
  ResearchPreparationJob as Job,
)
from quantx_infrastructure.models.research_preparation import (
  ResearchPreparationSettings as Settings,
)


def now():
  return datetime.now(timezone.utc).replace(tzinfo=None)


def root():
  return Path(
    os.environ.get("QUANTX_ROOT") or Path(__file__).resolve().parents[5]
  ).absolute()


def evidence_root():
  return Path(
    os.environ.get("QUANTX_RESEARCH_EVIDENCE_ROOT")
    or root() / ".runtime/research-evidence"
  ).absolute()


def reject_links(path: Path):
  for part in [*reversed(path.absolute().parents), path.absolute()]:
    if part.is_symlink() or (getattr(part, "is_junction", lambda: False)()):
      raise ValueError("研究文件目录不允许符号链接或联接点")


def evidence_file(reference: str):
  if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.(csv|parquet)", reference):
    raise ValueError("历史文件引用无效")
  path = evidence_root() / reference
  reject_links(path)
  if not path.is_file():
    raise ValueError("历史证据文件不存在，请先在运行端配置研究证据目录")
  return path


def list_evidence_files():
  directory = evidence_root()
  reject_links(directory)
  if not directory.is_dir():
    return []
  result = []
  for path in sorted(directory.iterdir()):
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.(csv|parquet)", path.name):
      try:
        evidence_file(path.name)
      except ValueError:
        continue
      result.append(path.name)
  return result


def digest(value):
  return hashlib.sha256(
    json.dumps(
      value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
  ).hexdigest()


def default_config():
  with (root() / "apps/research/configs/next_day_selection_v1.yaml").open(
    encoding="utf-8"
  ) as stream:
    data = yaml.safe_load(stream)["data"]
  return ResearchPreparationConfig(
    date_start=data["date_range"][0],
    date_end=data["date_range"][1],
    benchmark_code=data["benchmark_code"],
    minimum_listing_days=data["minimum_listing_days"],
    stock_codes=data.get("stock_codes") or [],
  ).model_dump(mode="json")


async def download_preview(config, calendar=None):
  from datetime import date

  from quantx_domain.indicators import INDICATOR_DEFINITIONS
  from quantx_domain.selection_factors import SELECTION_FACTOR_DEFINITIONS

  from quantx_infrastructure.services.trading_time_service import TradingDateHelper

  calendar = calendar or TradingDateHelper()
  ids = {
    key for factor in SELECTION_FACTOR_DEFINITIONS for key in factor.source_indicators
  }
  lookback = max(item.lookback for item in INDICATOR_DEFINITIONS if item.id in ids)
  start = config.date_start - timedelta(
    days=max(400, lookback * 2, config.minimum_listing_days * 2)
  )
  end = await calendar.get_next_trading_date("SH", config.date_end)
  return {
    "start": start.isoformat(),
    "end": end.isoformat(),
    "periods": ["1d"],
    "label_available": end <= date.today(),
    "warmup_days": (config.date_start - start).days,
    "benchmark": config.benchmark_code,
  }


class ResearchPreparationRepository:
  def __init__(self, db):
    self.db = db

  async def lock(self):
    if self.db.bind.dialect.name == "postgresql":
      await self.db.execute(text("SELECT pg_advisory_xact_lock(781462903)"))

  async def config(self):
    row = await self.db.get(Settings, 1)
    return dict(row.config) if row else default_config()

  async def save(self, payload):
    config = ResearchPreparationConfig.model_validate(payload).model_dump(mode="json")
    await self.lock()
    row = await self.db.get(Settings, 1)
    if row is None:
      row = Settings(id=1, config=config)
      self.db.add(row)
    else:
      row.config = config
    await self.db.commit()
    return config

  async def jobs(self, limit=30):
    return list(
      (
        await self.db.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit))
      ).all()
    )

  async def running_jobs(self, *, kinds):
    return list((await self.db.scalars(
      select(Job).where(Job.status == "RUNNING", Job.kind.in_(tuple(kinds)))
      .execution_options(populate_existing=True)
    )).all())

  async def submit(self, *, kind, config, request_key, dataset_version=None):
    if kind not in {"COVERAGE", "DOWNLOAD", "CERTIFY", "GPU"}:
      raise ValueError("未知准备任务类型")
    if not re.fullmatch(r"[A-Za-z0-9-]{16,80}", request_key):
      raise ValueError("请求标识无效")
    config = ResearchPreparationConfig.model_validate(config).model_dump(mode="json")
    if kind in {"CERTIFY", "GPU"} and not re.fullmatch(
      r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", dataset_version or ""
    ):
      raise ValueError("请选择或填写安全、唯一的数据集版本")
    if (
      kind in {"CERTIFY", "GPU"}
      and not all(config.get(k) for k in ("st_file", "industry_file", "delisting_file"))
      and kind == "CERTIFY"
    ):
      raise ValueError("完整认证需要先配置三类历史证据")
    request = {"config": config, "dataset_version": dataset_version}
    request_hash = digest({"kind": kind, "request": request, "key": request_key})
    await self.lock()
    existing = await self.db.scalar(select(Job).where(Job.request_hash == request_hash))
    if existing is not None:
      return existing
    active = (
      await self.db.scalars(
        select(Job).where(Job.kind == kind, Job.status.in_(["QUEUED", "RUNNING"]))
      )
    ).all()
    for existing in active:
      if all(existing.request.get(key) == value for key, value in request.items()):
        return existing
    row = Job(
      job_id=str(uuid.uuid4()),
      request_hash=request_hash,
      kind=kind,
      request=request,
      status="QUEUED",
      phase="等待 Trainer" if kind == "GPU" else "等待 Worker",
      result={},
      created_at=now(),
      updated_at=now(),
    )
    self.db.add(row)
    await self.db.commit()
    await self.db.refresh(row)
    return row

  async def retry(self, job_id):
    await self.lock()
    row = await self.db.get(Job, job_id)
    if row is None or row.status != "FAILED":
      raise ValueError("只能重试失败的准备任务")
    row.status, row.phase, row.error = "QUEUED", "等待重试", None
    row.updated_at = now()
    await self.db.commit()
    await self.db.refresh(row)
    return row

  async def claim(self, flow_run_id, *, kinds, prepare_execution=None):
    kinds = tuple(kinds)
    if not kinds or set(kinds) - {"COVERAGE", "DOWNLOAD", "CERTIFY", "GPU"}:
      raise ValueError("准备任务领取范围无效")
    flow_run_id = str(flow_run_id or "").strip()
    if not flow_run_id or len(flow_run_id) > 64:
      raise ValueError("准备任务领取必须提供有效执行归属")
    await self.lock()
    # A stale heartbeat does not prove that a process or download has stopped.
    active = await self.db.scalar(
      select(Job.job_id).where(Job.status == "RUNNING").limit(1)
    )
    if active:
      await self.db.commit()
      return None
    row = await self.db.scalar(
      select(Job)
      .where(Job.status == "QUEUED", Job.kind.in_(kinds))
      .order_by(Job.created_at)
      .with_for_update(skip_locked=True)
      .limit(1)
    )
    if row:
      if prepare_execution is not None:
        try:
          prepare_execution(row.job_id, flow_run_id)
        except BaseException:
          await self.db.rollback()
          raise
      row.status, row.phase, row.flow_run_id, row.updated_at = (
        "RUNNING",
        "准备执行",
        flow_run_id,
        now(),
      )
    await self.db.commit()
    if row:
      await self.db.refresh(row)
    return row

  async def requeue_gpu_admission(self, job_id, *, expected_flow_run_id):
    """Requeue only after the supervisor verifies a host-admission exit."""
    await self._requeue_gpu(job_id, expected_flow_run_id=expected_flow_run_id, phase="等待主机资源")

  async def requeue_gpu_inputs(self, job_id, *, expected_flow_run_id):
    """Requeue only after input-only execution is proven stopped."""
    await self._requeue_gpu(job_id, expected_flow_run_id=expected_flow_run_id, phase="恢复输入准备")

  async def _requeue_gpu(self, job_id, *, expected_flow_run_id, phase):
    if not expected_flow_run_id:
      raise ValueError("准备任务执行归属无效")
    result = await self.db.execute(
      update(Job).where(Job.job_id == job_id, Job.kind == "GPU", Job.status == "RUNNING",
                        Job.flow_run_id == expected_flow_run_id)
      .values(status="QUEUED", phase=phase, flow_run_id=None,
              error=None, updated_at=now())
    )
    if result.rowcount != 1:
      await self.db.rollback()
      raise ValueError("准备任务执行归属已变化或任务已结束")
    await self.db.commit()

  async def progress(self, job_id, *, expected_flow_run_id, **values):
    if not expected_flow_run_id or set(values) - {"status", "phase", "error", "result", "request"}:
      raise ValueError("准备任务更新参数或执行归属无效")
    if "status" in values and values["status"] not in {"RUNNING", "SUCCEEDED", "FAILED"}:
      raise ValueError("准备任务执行者不能重新排队")
    result = await self.db.execute(
      update(Job)
      .where(Job.job_id == job_id, Job.status == "RUNNING", Job.flow_run_id == expected_flow_run_id)
      .values(updated_at=now(), **values)
    )
    if result.rowcount != 1:
      await self.db.rollback()
      raise ValueError("准备任务执行归属已变化或任务已结束")
    await self.db.commit()
