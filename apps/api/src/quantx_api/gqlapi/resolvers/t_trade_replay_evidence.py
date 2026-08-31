"""Read-only, exact-version replay evidence; no latest-version fallback."""

import asyncio
import base64
import hashlib
import heapq
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import strawberry
from quantx_infrastructure.core.t_trade_replay_evidence import (
  SIGNAL_EVENT_TYPES,
  ReplayEvidenceUnavailable,
  artifact_path,
  file_fingerprint,
  iter_jsonl,
  read_manifest,
  sealed_opportunity_path,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.strategy_decision_trace_record import (
  StrategyDecisionTraceRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.backtest_repository import BacktestRepository
from quantx_infrastructure.repositories.strategy_run_repository import (
  StrategyRunRepository,
)
from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
  TTradeOpportunityEvaluationRepository,
)
from sqlalchemy import and_, or_, select

from ..security import authorized_account_id
from ..types.common_types import PageInfo
from ..types.strategy_types import ExecutionTraceView, StrategyDecision
from ..types.t_trade_replay_evidence_types import (
  TTradeReplayAuditFilterInput,
  TTradeReplayAuditItem,
  TTradeReplayAuditPage,
  TTradeReplayAuditSummary,
  TTradeReplayEvidenceAvailability,
  TTradeReplayEvidenceInfo,
  TTradeReplayEvidenceSource,
  TTradeReplaySignalFilterInput,
  TTradeReplaySignalPage,
  TTradeReplaySignalSummary,
)
from ..types.t_trade_types import TTradeSignalEvaluation, TTradeSignalEvaluationKind
from .t_trade import TTradeResolver


def _datetime(value) -> datetime:
  if isinstance(value, datetime):
    result = value
  else:
    try:
      result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
      raise ReplayEvidenceUnavailable("ARCHIVE_INTEGRITY_FAILED") from exc
  # QuantX's naive persisted timestamps are Asia/Shanghai, never UTC.
  # Keep an explicit China offset on the wire, including for legacy archives.
  return time_utils.to_shanghai(result, keep_tz=True)


def _key(record: dict, kind: str) -> tuple[str, str]:
  value = record.get("evaluated_at") if kind == "signal" else record.get("timestamp")
  timestamp = (
    _datetime(value).astimezone(timezone.utc).isoformat(timespec="microseconds")
  )
  identifier = str(record.get("id") or record.get("trace_id") or "")
  if not identifier:
    raise ReplayEvidenceUnavailable("ARCHIVE_INTEGRITY_FAILED")
  return timestamp, identifier


def _scope(info: TTradeReplayEvidenceInfo, filters, kind: str) -> str:
  encoded = json.dumps(
    {
      "run": info.run_id,
      "backtest": info.backtest_id,
      "kind": kind,
      "source": info.source.value,
      "fingerprint": info.content_fingerprint,
      "filters": asdict(filters),
    },
    sort_keys=True,
  )
  return hashlib.sha256(encoded.encode()).hexdigest()


def _cursor(scope: str, key: tuple[str, str]) -> str:
  return base64.urlsafe_b64encode(json.dumps([scope, *key]).encode()).decode()


def _after(value: str | None, scope: str) -> tuple[str, str] | None:
  if not value:
    return None
  try:
    data = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
    if not isinstance(data, list) or len(data) != 3 or data[0] != scope:
      raise ValueError()
    if not all(isinstance(item, str) for item in data):
      raise ValueError()
    _datetime(data[1])
    return data[1], data[2]
  except (ValueError, TypeError) as exc:
    raise ValueError("回放游标与当前版本或筛选不匹配，请刷新第一页") from exc


class _Page:
  """Keep only page-size records while scanning evidence for exact summaries."""

  def __init__(self, first: int, after: str | None, scope: str, kind: str):
    if not 1 <= first <= 100:
      raise ValueError("回放证据分页条数必须在 1 到 100 之间")
    self.first, self.scope, self.kind = first, scope, kind
    self.after = _after(after, scope)
    self.heap = []
    self.sequence = 0

  def add(self, record: dict):
    key = _key(record, self.kind)
    if self.after and key >= self.after:
      return
    self.sequence += 1
    item = (key, self.sequence, record)
    if len(self.heap) <= self.first:
      heapq.heappush(self.heap, item)
    elif key > self.heap[0][0]:
      heapq.heapreplace(self.heap, item)

  def finish(self):
    ordered = sorted(self.heap, reverse=True)
    selected = ordered[: self.first]
    return [item[2] for item in selected], PageInfo(
      has_next_page=len(ordered) > self.first,
      has_previous_page=self.after is not None,
      start_cursor=_cursor(self.scope, selected[0][0]) if selected else None,
      end_cursor=_cursor(self.scope, selected[-1][0]) if selected else None,
    )


def _signal_identity(record: dict):
  payload = record.get("payload") or {}
  snapshot = payload.get("signal_snapshot") or {}
  candidate = record.get("candidate_id") or snapshot.get("candidate_id")
  intent = (payload.get("intent_link") or {}).get("intent_id") or snapshot.get(
    "pending_entry_intent_id"
  )
  return snapshot, candidate, intent


def _category(record: dict) -> str:
  if record.get("record_kind") == "COALESCED_DIAGNOSTIC":
    return "DIAGNOSTIC"
  return "SIGNAL" if record.get("event_type") in SIGNAL_EVENT_TYPES else "CONTEXT"


def _matches_signal(record: dict, filters: TTradeReplaySignalFilterInput) -> bool:
  category = _category(record)
  if category == "DIAGNOSTIC" and not filters.include_diagnostics:
    return False
  if category == "CONTEXT" and not filters.include_context:
    return False
  snapshot, candidate, intent = _signal_identity(record)
  if filters.stock_code and record.get("instrument_code") != filters.stock_code:
    return False
  if filters.event_types and record.get("event_type") not in filters.event_types:
    return False
  for expected, actual in (
    (filters.selected_path, snapshot.get("selected_path")),
    (filters.candidate_status, snapshot.get("candidate_status")),
    (filters.candidate_id, candidate),
    (filters.event_key, record.get("event_key")),
  ):
    if expected and expected != actual:
      return False
  return (
    not filters.search
    or filters.search.casefold()
    in " ".join(
      str(item or "")
      for item in (
        record.get("instrument_code"),
        record.get("event_type"),
        candidate,
        intent,
        record.get("event_key"),
        snapshot.get("top_blockers"),
      )
    ).casefold()
  )


def _signal_type(record: dict) -> TTradeSignalEvaluation:
  snapshot, candidate, intent = _signal_identity(record)
  return TTradeSignalEvaluation(
    id=strawberry.ID(str(record["id"])),
    event_key=record["event_key"],
    category=_category(record),
    candidate_id=candidate,
    linked_intent_id=intent,
    account_id=record["account_id"],
    run_id=strawberry.ID(record["strategy_run_id"]),
    stock_code=record["instrument_code"],
    event_kind=TTradeSignalEvaluationKind(record["record_kind"]),
    event_type=record["event_type"],
    evaluated_at=_datetime(record["evaluated_at"]),
    window_started_at=_datetime(record["window_started_at"])
    if record.get("window_started_at")
    else None,
    window_ended_at=_datetime(record["window_ended_at"])
    if record.get("window_ended_at")
    else None,
    coalesced_count=record["coalesced_count"],
    policy_version=record["policy_version"],
    schema_version=record["schema_version"],
    content_fingerprint=record["content_fingerprint"],
    signal_snapshot=TTradeResolver._signal_snapshot_type(snapshot)
    if snapshot
    else None,
  )


def _event_keys(record: dict) -> list[str]:
  references = (record.get("output_summary") or {}).get("evaluation_references") or []
  return list(
    dict.fromkeys(
      item["evaluation_event_key"]
      for item in references
      if isinstance(item, dict) and isinstance(item.get("evaluation_event_key"), str)
    )
  )


def _intent_ids(record: dict) -> set[str]:
  return {
    str(item.get("id") or item.get("intent_id"))
    for item in record.get("trade_intents") or []
    if item.get("id") or item.get("intent_id")
  }


def _audit_type(
  record: dict, executions: dict[str, ExecutionTraceView]
) -> TTradeReplayAuditItem:
  decision = StrategyDecision.from_backtest_record(record)
  if record.get("instrument_code"):
    decision.input_summary.setdefault("instrument_code", record["instrument_code"])
  # Backtest event time is authoritative; never substitute wall-clock 'now'.
  decision.decided_at = _datetime(record["timestamp"])
  return TTradeReplayAuditItem(
    decision=decision,
    evaluation_event_keys=_event_keys(record),
    executions=[
      executions[key] for key in sorted(_intent_ids(record)) if key in executions
    ],
  )


async def _context(db, info, run_id: str, backtest_id: str):
  run = await StrategyRunRepository(db).find_run_by_id(run_id)
  backtest = await BacktestRepository(db).get_backtest(backtest_id)
  if not run or not backtest or str(backtest.strategy_run_id) != run_id:
    raise ValueError("回放与回测版本不匹配")
  parameters = dict(run.parameters or {})
  frozen = dict(backtest.parameters or {})
  account_id = str(parameters.get("account_id") or "")
  if (
    not account_id
    or not parameters.get("t_trade_replay")
    or not frozen.get("t_trade_replay")
    or frozen.get("account_id") != account_id
    or str(getattr(run.mode, "value", run.mode)).upper() != "BACKTEST"
  ):
    raise ValueError("不是有效的账户做 T 回放")
  authorized_account_id(info, account_id)
  active = str(backtest.status).upper() in {"PENDING", "RUNNING", "STARTING"}
  if active:
    await _assert_current(db, run_id, backtest_id)
  evidence = TTradeReplayEvidenceInfo(
    run_id=run_id,
    backtest_id=backtest_id,
    backtest_version=int(backtest.version),
    availability=TTradeReplayEvidenceAvailability.AVAILABLE,
    source=(
      TTradeReplayEvidenceSource.RUN_PROJECTION
      if active
      else TTradeReplayEvidenceSource.VERSION_ARCHIVE
    ),
    sealed=False,
  )
  return account_id, backtest, evidence


async def _assert_current(db, run_id: str, backtest_id: str):
  latest = (await BacktestRepository(db).get_latest_backtests_by_runs([run_id])).get(
    run_id
  )
  if latest is None or str(latest.id) != backtest_id:
    raise ValueError("回放版本已切换，请刷新后读取精确版本")


def _manifest(backtest, evidence):
  path = Path(str(backtest.result_path or ""))
  # Resolve only the selected version's saved path, never another run/latest.
  path = path if path.is_file() else Path("data") / path
  manifest = read_manifest(
    str(path),
    run_id=evidence.run_id,
    backtest_id=evidence.backtest_id,
    version=evidence.backtest_version,
  )
  return str(path), manifest


def _unavailable(evidence, exc):
  evidence.availability = TTradeReplayEvidenceAvailability.UNAVAILABLE
  evidence.reason_code = str(exc)
  evidence.sealed = False
  return PageInfo(
    has_next_page=False, has_previous_page=False, start_cursor=None, end_cursor=None
  )


class TTradeReplayEvidenceResolver:
  @staticmethod
  async def signals(
    info,
    run_id: str,
    backtest_id: str,
    filters: TTradeReplaySignalFilterInput | None,
    first: int,
    after: str | None,
  ):
    filters = filters or TTradeReplaySignalFilterInput()
    async with AsyncSessionLocal() as db:
      account_id, backtest, evidence = await _context(db, info, run_id, backtest_id)
      try:
        if evidence.source == TTradeReplayEvidenceSource.VERSION_ARCHIVE:

          def load():
            path, manifest = _manifest(backtest, evidence)
            archive = sealed_opportunity_path(path, manifest, account_id=account_id)
            evidence.sealed = True
            evidence.content_fingerprint = manifest["opportunity_evaluations"][
              "content_fingerprint"
            ]
            collector = _SignalCollector(
              evidence, filters, first, after, account_id=account_id
            )
            count = 0
            for record in iter_jsonl(archive):
              collector.add(record)
              count += 1
            if count != manifest["opportunity_evaluations"].get("count"):
              raise ReplayEvidenceUnavailable("ARCHIVE_INTEGRITY_FAILED")
            return collector.finish()

          return await asyncio.to_thread(load)
        collector = _SignalCollector(
          evidence, filters, first, after, account_id=account_id
        )
        async for record in TTradeOpportunityEvaluationRepository(
          db
        ).iter_run_evaluations(
          account_id=account_id,
          strategy_run_id=run_id,
        ):
          collector.add(record)
        await _assert_current(db, run_id, backtest_id)
        return collector.finish()
      except ReplayEvidenceUnavailable as exc:
        return TTradeReplaySignalPage(
          evidence=evidence,
          items=[],
          summary=TTradeReplaySignalSummary(),
          page_info=_unavailable(evidence, exc),
        )

  @staticmethod
  async def audit(
    info,
    run_id: str,
    backtest_id: str,
    filters: TTradeReplayAuditFilterInput | None,
    first: int,
    after: str | None,
  ):
    filters = filters or TTradeReplayAuditFilterInput()
    async with AsyncSessionLocal() as db:
      _, backtest, evidence = await _context(db, info, run_id, backtest_id)
      try:
        if evidence.source == TTradeReplayEvidenceSource.VERSION_ARCHIVE:

          def load():
            path, manifest = _manifest(backtest, evidence)
            if manifest.get("schema_version") not in {3, 4}:
              raise ReplayEvidenceUnavailable("AUDIT_ARCHIVE_NOT_RECORDED")
            if (
              manifest.get("schema_version") == 4 and manifest.get("sealed") is not True
            ):
              raise ReplayEvidenceUnavailable("ARCHIVE_NOT_SEALED")
            decisions_path = artifact_path(path, manifest, "decision_events")
            executions_path = artifact_path(path, manifest, "execution_summary")
            fingerprints = {
              key: file_fingerprint(item)
              for key, item in (
                ("decision_events", decisions_path),
                ("execution_summary", executions_path),
              )
            }
            if manifest.get("schema_version") == 4 and any(
              (manifest.get("artifact_fingerprints") or {}).get(key) != value
              for key, value in fingerprints.items()
            ):
              raise ReplayEvidenceUnavailable("ARCHIVE_INTEGRITY_FAILED")
            evidence.sealed = manifest.get("schema_version") == 4
            evidence.content_fingerprint = hashlib.sha256(
              json.dumps(fingerprints, sort_keys=True).encode()
            ).hexdigest()
            executions = {
              str(
                item.get("id") or item.get("intent_id")
              ): ExecutionTraceView.from_backtest_intent(item)
              for item in iter_jsonl(executions_path)
            }
            collector = _AuditCollector(evidence, filters, first, after, executions)
            for record in _archive_decisions(decisions_path, evidence.backtest_id):
              collector.add(record)
            return collector.finish()

          return await asyncio.to_thread(load)
        intent_rows = (
          (
            await db.execute(
              select(TradeIntentRecord).where(
                TradeIntentRecord.strategy_run_id == run_id,
                TradeIntentRecord.created_at >= backtest.created_at,
              )
            )
          )
          .scalars()
          .all()
        )
        executions = {
          str(row.id): ExecutionTraceView.from_intent(row) for row in intent_rows
        }
        collector = _AuditCollector(evidence, filters, first, after, executions)
        cursor_at = cursor_id = None
        model = StrategyDecisionTraceRecord
        while True:
          conditions = [
            model.strategy_run_id == run_id,
            model.created_at >= backtest.created_at,
          ]
          if cursor_at is not None:
            conditions.append(
              or_(
                model.decided_at < cursor_at,
                and_(model.decided_at == cursor_at, model.id < cursor_id),
              )
            )
          rows = (
            (
              await db.execute(
                select(model)
                .where(*conditions)
                .order_by(
                  model.decided_at.desc(),
                  model.id.desc(),
                )
                .limit(500)
              )
            )
            .scalars()
            .all()
          )
          if not rows:
            break
          for row in rows:
            collector.add(
              {
                **dict(row.decision_trace or {}),
                **row.to_dict(),
                "run_id": run_id,
                "timestamp": row.decided_at.isoformat(),
              }
            )
          cursor_at, cursor_id = rows[-1].decided_at, rows[-1].id
        await _assert_current(db, run_id, backtest_id)
        return collector.finish()
      except ReplayEvidenceUnavailable as exc:
        return TTradeReplayAuditPage(
          evidence=evidence,
          items=[],
          summary=TTradeReplayAuditSummary(),
          page_info=_unavailable(evidence, exc),
        )


class _SignalCollector:
  def __init__(self, evidence, filters, first, after, *, account_id: str):
    self.evidence, self.filters = evidence, filters
    self.account_id = account_id
    self.page = _Page(first, after, _scope(evidence, filters, "signal"), "signal")
    self.summary = TTradeReplaySignalSummary()
    self.candidates, self.intents = set(), set()

  def add(self, record):
    if (
      record.get("strategy_run_id") != self.evidence.run_id
      or record.get("account_id") != self.account_id
    ):
      raise ReplayEvidenceUnavailable("ARCHIVE_IDENTITY_MISMATCH")
    if not _matches_signal(record, self.filters):
      return
    self.summary.event_count += 1
    _, candidate, intent = _signal_identity(record)
    if candidate:
      self.candidates.add(candidate)
    if intent:
      self.intents.add(intent)
    if record.get("event_type") == "CANDIDATE_SUPPRESSED":
      self.summary.suppressed_count += 1
    self.page.add(record)

  def finish(self):
    self.summary.candidate_count, self.summary.linked_intent_count = (
      len(self.candidates),
      len(self.intents),
    )
    records, page_info = self.page.finish()
    return TTradeReplaySignalPage(
      evidence=self.evidence,
      items=[_signal_type(item) for item in records],
      summary=self.summary,
      page_info=page_info,
    )


def _archive_decisions(path: Path, backtest_id: str):
  # trace_id groups multiple strategy/sizing/risk records, so it is not a row ID.
  # The ordinal is stable within this sealed, fingerprint-bound version only.
  for ordinal, record in enumerate(iter_jsonl(path), start=1):
    yield {**record, "id": f"{backtest_id}:decision:{ordinal}"}


class _AuditCollector:
  def __init__(self, evidence, filters, first, after, executions):
    self.evidence, self.filters, self.executions = evidence, filters, executions
    self.page = _Page(first, after, _scope(evidence, filters, "audit"), "audit")
    self.summary = TTradeReplayAuditSummary()

  def add(self, record):
    if (
      str(record.get("strategy_run_id") or record.get("run_id")) != self.evidence.run_id
    ):
      raise ReplayEvidenceUnavailable("ARCHIVE_IDENTITY_MISMATCH")
    filters = self.filters
    intents = _intent_ids(record)
    if filters.stock_code and filters.stock_code != (
      record.get("instrument_code")
      or (record.get("input_summary") or {}).get("instrument_code")
    ):
      return
    if filters.with_intent is not None and filters.with_intent != bool(intents):
      return
    if filters.event_key and filters.event_key not in _event_keys(record):
      return
    if filters.execution_status and not any(
      self.executions.get(key)
      and self.executions[key].order_status == filters.execution_status
      for key in intents
    ):
      return
    if (
      filters.search
      and filters.search.casefold()
      not in json.dumps(record, ensure_ascii=False).casefold()
    ):
      return
    self.summary.decision_count += 1
    self.summary.with_intent_count += bool(intents)
    self.summary.no_intent_count += not intents
    if (
      "risk_blocked" in (record.get("tags") or [])
      or (record.get("risk_decision") or {}).get("allowed") is False
    ):
      self.summary.risk_blocked_count += 1
    self.page.add(record)

  def finish(self):
    records, page_info = self.page.finish()
    return TTradeReplayAuditPage(
      evidence=self.evidence,
      items=[_audit_type(item, self.executions) for item in records],
      summary=self.summary,
      page_info=page_info,
    )
