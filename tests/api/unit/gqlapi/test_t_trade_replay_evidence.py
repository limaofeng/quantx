import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_api.gqlapi.resolvers import t_trade_replay_evidence as resolver
from quantx_api.gqlapi.types.t_trade_replay_evidence_types import (
  TTradeReplayAuditFilterInput,
  TTradeReplayEvidenceAvailability,
  TTradeReplayEvidenceInfo,
  TTradeReplayEvidenceSource,
  TTradeReplaySignalFilterInput,
)
from quantx_infrastructure.core.backtest_result_storage import BacktestResultStorage


def evidence(backtest_id="bt-1"):
  return TTradeReplayEvidenceInfo(
    run_id="run-1",
    backtest_id=backtest_id,
    backtest_version=1,
    availability=TTradeReplayEvidenceAvailability.AVAILABLE,
    source=TTradeReplayEvidenceSource.VERSION_ARCHIVE,
    sealed=True,
    content_fingerprint="sealed-fingerprint",
  )


def test_replay_web_operations_match_the_source_schema():
  """Offline contract check; the release gate still requires Caddy codegen."""
  from graphql import parse, validate
  from quantx_api.gqlapi.schema import schema

  root = Path(__file__).resolve().parents[4]
  hooks = root / "apps/web/src/features/portfolio/hooks"
  operations = re.findall(
    r"gql\(`([\s\S]*?)`\)",
    (hooks / "useTTradeReplayEvidence.ts").read_text(encoding="utf-8"),
  )
  snapshot = re.search(
    r"TTradeSignalSnapshotFieldsFragment = gql\(`([\s\S]*?)`\)",
    (hooks / "useTTradeGlobal.ts").read_text(encoding="utf-8"),
  )
  assert len(operations) == 2 and snapshot is not None
  errors = validate(schema._schema, parse("\n".join([*operations, snapshot.group(1)])))
  assert not errors, [str(error) for error in errors]


def signal(index, event_type="CANDIDATE_SUPPRESSED", kind="MATERIAL"):
  return {
    "id": f"{index:04d}",
    "event_key": f"event-{index}",
    "account_id": "account-1",
    "strategy_run_id": "run-1",
    "instrument_code": "600000.SH",
    "candidate_id": "candidate-1",
    "evaluated_at": "2026-08-28T02:00:00+00:00",
    "record_kind": kind,
    "event_type": event_type,
    "payload": {},
    "coalesced_count": 1,
    "schema_version": "3",
    "policy_version": "3",
    "content_fingerprint": f"fingerprint-{index}",
  }


@pytest.mark.parametrize(
  "timestamp",
  [
    "2026-08-03T09:30:00",
    "2026-08-03T01:30:00Z",
    "2026-08-03T09:30:00+08:00",
    datetime(2026, 8, 3, 9, 30),
  ],
)
def test_signal_and_audit_times_use_china_timezone(timestamp):
  record = {**signal(1), "evaluated_at": timestamp}
  item = resolver._signal_type(record)
  assert item.evaluated_at.isoformat() == "2026-08-03T09:30:00+08:00"
  assert item.evaluated_at.utcoffset() == timedelta(hours=8)
  audit = resolver._audit_type(
    {
      "id": "decision-1",
      "run_id": "run-1",
      "timestamp": timestamp,
      "trade_intents": [],
    },
    {},
  )
  assert audit.decision.decided_at == item.evaluated_at
  assert resolver._key(record, "signal")[0] == "2026-08-03T01:30:00.000000+00:00"


def test_exact_audit_backlink_can_read_context_without_reclassifying_it():
  collector = resolver._SignalCollector(
    evidence(),
    TTradeReplaySignalFilterInput(event_key="event-1", include_context=True),
    50,
    None,
    account_id="account-1",
  )
  collector.add(signal(1, "POLICY_CHANGED"))
  collector.add(signal(2))
  page = collector.finish()
  assert [(item.event_key, item.category) for item in page.items] == [
    ("event-1", "CONTEXT"),
  ]
  assert page.summary.event_count == 1


def test_true_signals_include_suppressed_candidate_without_intent():
  collector = resolver._SignalCollector(
    evidence(), TTradeReplaySignalFilterInput(), 50, None, account_id="account-1"
  )
  for item in [
    signal(1),
    signal(2, "POLICY_CHANGED"),
    signal(3, "COALESCED_DIAGNOSTIC", "COALESCED_DIAGNOSTIC"),
  ]:
    collector.add(item)
  page = collector.finish()
  assert [item.event_key for item in page.items] == ["event-1"]
  assert page.items[0].linked_intent_id is None
  assert page.summary.event_count == 1
  assert page.summary.candidate_count == 1
  assert page.summary.suppressed_count == 1


def test_keyset_pages_cross_200_equal_timestamps_without_loss_or_duplicates():
  after = None
  seen = []
  while True:
    collector = resolver._SignalCollector(
      evidence(), TTradeReplaySignalFilterInput(), 50, after, account_id="account-1"
    )
    for index in range(237):
      collector.add(signal(index))
    page = collector.finish()
    assert page.summary.event_count == 237
    seen.extend(str(item.id) for item in page.items)
    if not page.page_info.has_next_page:
      break
    after = page.page_info.end_cursor
  assert seen == [f"{index:04d}" for index in reversed(range(237))]


def test_cursor_is_bound_to_exact_version_and_filter():
  collector = resolver._SignalCollector(
    evidence(), TTradeReplaySignalFilterInput(), 1, None, account_id="account-1"
  )
  collector.add(signal(1))
  after = collector.finish().page_info.end_cursor
  with pytest.raises(ValueError, match="游标"):
    resolver._SignalCollector(
      evidence("bt-2"),
      TTradeReplaySignalFilterInput(),
      1,
      after,
      account_id="account-1",
    )
  with pytest.raises(ValueError, match="游标"):
    resolver._SignalCollector(
      evidence(),
      TTradeReplaySignalFilterInput(stock_code="600001.SH"),
      1,
      after,
      account_id="account-1",
    )


def test_activity_opt_in_includes_real_context_and_diagnostics():
  collector = resolver._SignalCollector(
    evidence(),
    TTradeReplaySignalFilterInput(
      include_context=True,
      include_diagnostics=True,
    ),
    50,
    None,
    account_id="account-1",
  )
  collector.add(signal(1, "POLICY_CHANGED"))
  collector.add(signal(2, "COALESCED_DIAGNOSTIC", "COALESCED_DIAGNOSTIC"))
  assert {item.category for item in collector.finish().items} == {
    "CONTEXT",
    "DIAGNOSTIC",
  }


def test_audit_links_exact_event_key_and_keeps_no_intent_decision():
  collector = resolver._AuditCollector(
    evidence(), TTradeReplayAuditFilterInput(event_key="event-1"), 50, None, {}
  )
  for index in (1, 2):
    collector.add(
      {
        "id": str(index),
        "trace_id": f"trace-{index}",
        "run_id": "run-1",
        "instrument_code": "600000.SH",
        "timestamp": "2026-08-28T02:00:00Z",
        "trade_intents": [],
        "output_summary": {
          "evaluation_references": [
            {"evaluation_event_key": f"event-{index}"},
          ]
        },
      }
    )
  page = collector.finish()
  assert page.summary.no_intent_count == 1
  assert page.items[0].evaluation_event_keys == ["event-1"]
  assert page.items[0].decision.input_summary["instrument_code"] == "600000.SH"
  assert page.items[0].executions == []


def test_signal_record_cannot_cross_account_even_inside_a_valid_manifest():
  collector = resolver._SignalCollector(
    evidence(), TTradeReplaySignalFilterInput(), 50, None, account_id="account-1"
  )
  with pytest.raises(resolver.ReplayEvidenceUnavailable, match="IDENTITY_MISMATCH"):
    collector.add({**signal(1), "account_id": "account-2"})


def test_audit_archive_does_not_drop_records_that_share_one_trace(tmp_path):
  path = tmp_path / "decision_events.jsonl"
  record = {
    "id": "shared-trace",
    "trace_id": "shared-trace",
    "run_id": "run-1",
    "timestamp": "2026-08-28T02:00:00Z",
    "trade_intents": [],
  }
  path.write_text("\n".join(json.dumps(record) for _ in range(237)), encoding="utf-8")
  after, seen = None, []
  while True:
    collector = resolver._AuditCollector(
      evidence(), TTradeReplayAuditFilterInput(), 50, after, {}
    )
    for item in resolver._archive_decisions(path, "bt-1"):
      collector.add(item)
    page = collector.finish()
    seen.extend(item.decision.id for item in page.items)
    assert page.summary.decision_count == 237
    assert all(item.decision.trace_id == "shared-trace" for item in page.items)
    if not page.page_info.has_next_page:
      break
    after = page.page_info.end_cursor
  assert len(seen) == len(set(seen)) == 237


def test_risk_count_uses_risk_facts_not_a_sizing_or_candidate_rejection():
  collector = resolver._AuditCollector(
    evidence(),
    TTradeReplayAuditFilterInput(),
    50,
    None,
    {
      "intent-1": SimpleNamespace(order_status="REJECTED"),
    },
  )
  base = {
    "run_id": "run-1",
    "timestamp": "2026-08-28T02:00:00Z",
    "trade_intents": [{"intent_id": "intent-1"}],
  }
  collector.add({**base, "id": "size", "reason": "ZERO_SIZED_VOLUME"})
  assert collector.summary.risk_blocked_count == 0
  collector.add({**base, "id": "risk", "risk_decision": {"allowed": False}})
  assert collector.summary.risk_blocked_count == 1


@pytest.mark.asyncio
async def test_context_checks_run_version_account_and_never_selects_latest(monkeypatch):
  run = SimpleNamespace(
    id="run-1",
    mode="BACKTEST",
    parameters={
      "account_id": "account-1",
      "t_trade_replay": True,
    },
  )
  backtest = SimpleNamespace(
    id="bt-1",
    strategy_run_id="another-run",
    status="COMPLETED",
    version=1,
    parameters=dict(run.parameters),
  )
  monkeypatch.setattr(
    resolver,
    "StrategyRunRepository",
    lambda db: SimpleNamespace(find_run_by_id=AsyncMock(return_value=run)),
  )
  monkeypatch.setattr(
    resolver,
    "BacktestRepository",
    lambda db: SimpleNamespace(get_backtest=AsyncMock(return_value=backtest)),
  )
  authorized = []
  monkeypatch.setattr(
    resolver, "authorized_account_id", lambda info, account: authorized.append(account)
  )
  with pytest.raises(ValueError, match="版本不匹配"):
    await resolver._context(None, None, "run-1", "bt-1")
  backtest.strategy_run_id = "run-1"
  await resolver._context(None, None, "run-1", "bt-1")
  assert authorized == ["account-1"]
  backtest.parameters["account_id"] = "account-2"
  with pytest.raises(ValueError, match="有效"):
    await resolver._context(None, None, "run-1", "bt-1")


@pytest.mark.asyncio
async def test_terminal_signal_page_reads_only_its_sealed_version(
  tmp_path, monkeypatch
):
  async def records():
    yield signal(1)
    yield signal(2, "POLICY_CHANGED")

  storage = BacktestResultStorage("bt-1", str(tmp_path), "run-1", 1)
  await storage.archive_opportunity_evaluations(records(), account_id="account-1")
  manifest_path = await storage.flush()

  @asynccontextmanager
  async def session():
    yield None

  def unexpected_live_read(db):
    raise AssertionError("A terminal version must not read the mutable projection")

  monkeypatch.setattr(resolver, "AsyncSessionLocal", session)
  monkeypatch.setattr(
    resolver, "TTradeOpportunityEvaluationRepository", unexpected_live_read
  )
  monkeypatch.setattr(
    resolver,
    "_context",
    AsyncMock(
      return_value=(
        "account-1",
        SimpleNamespace(result_path=manifest_path),
        evidence(),
      )
    ),
  )
  page = await resolver.TTradeReplayEvidenceResolver.signals(
    None, "run-1", "bt-1", None, 50, None
  )
  assert page.evidence.source == resolver.TTradeReplayEvidenceSource.VERSION_ARCHIVE
  assert page.evidence.sealed is True
  assert [item.event_key for item in page.items] == ["event-1"]
  assert page.summary.event_count == 1

  # The same archive with incorrect manifest count is unavailable, not empty.
  manifest = json.loads(
    (tmp_path / "run-1/v1/manifest.json").read_text(encoding="utf-8")
  )
  manifest["opportunity_evaluations"]["count"] = 999
  (tmp_path / "run-1/v1/manifest.json").write_text(
    json.dumps(manifest), encoding="utf-8"
  )
  page = await resolver.TTradeReplayEvidenceResolver.signals(
    None, "run-1", "bt-1", None, 50, None
  )
  assert (
    page.evidence.availability == resolver.TTradeReplayEvidenceAvailability.UNAVAILABLE
  )
  assert page.evidence.reason_code == "ARCHIVE_INTEGRITY_FAILED"
  assert page.items == []


@pytest.mark.asyncio
async def test_legacy_signal_is_unavailable_while_original_audit_stays_readable(
  tmp_path, monkeypatch
):
  storage = BacktestResultStorage("bt-1", str(tmp_path), "run-1", 1)
  storage.add_trace(
    {
      "trace_id": "existing-trace",
      "run_id": "run-1",
      "instrument_code": "600000.SH",
      "timestamp": "2026-08-28T02:00:00Z",
      "reason": "CANDIDATE_SUPPRESSED",
      "trade_intents": [],
      "tags": ["candidate_suppressed"],
    }
  )
  manifest_path = await storage.flush()
  path = tmp_path / "run-1/v1/manifest.json"
  manifest = json.loads(path.read_text(encoding="utf-8"))
  manifest["schema_version"] = 3
  manifest.pop("sealed")
  manifest.pop("opportunity_evaluations")
  manifest.pop("artifact_fingerprints")
  path.write_text(json.dumps(manifest), encoding="utf-8")

  @asynccontextmanager
  async def session():
    yield None

  async def selected_context(*args):
    return "account-1", SimpleNamespace(result_path=manifest_path), evidence()

  monkeypatch.setattr(resolver, "AsyncSessionLocal", session)
  monkeypatch.setattr(resolver, "_context", selected_context)
  signals = await resolver.TTradeReplayEvidenceResolver.signals(
    None, "run-1", "bt-1", None, 50, None
  )
  assert signals.evidence.reason_code == "SIGNAL_ARCHIVE_NOT_RECORDED"
  audit = await resolver.TTradeReplayEvidenceResolver.audit(
    None, "run-1", "bt-1", None, 50, None
  )
  assert (
    audit.evidence.availability == resolver.TTradeReplayEvidenceAvailability.AVAILABLE
  )
  assert audit.summary.no_intent_count == 1
  assert audit.evidence.sealed is False
  assert audit.items[0].decision.trace_id == "existing-trace"
