from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IOS_ROOT = ROOT / "apps" / "ios"
PLAN = ROOT / "docs" / "plans" / "持仓做T有状态机会引擎V3实施规格.md"

CORE_V3_IOS_SYMBOLS = (
  "TTradeCandidateApprovalExpectationInput",
  "TTradeSignalSnapshot",
  "TTradeCandidateStatus",
  "approvalUnavailableReason",
  "最近一次刷新失败，禁止基于旧信号快照确认",
)

DEFERRED_V3_IOS_SYMBOLS = (
  "IOSTTradeSignalEvaluationsQuery",
  "IOSTTradeSignalDiagnosticsQuery",
  "IOSTTradeCandidateTraceQuery",
  "IOSTTradeUpdatesSubscription",
  "IOSRecordTTradeClientTelemetryMutation",
  "IOSTTradeSignalSnapshotFields",
  "TTradeSignalEvaluationKind",
  "TTradeOpportunitySnapshot",
  "TTradeCandidateTrace",
  "TTradeClientTelemetry",
)


def test_v3_ios_core_client_is_present_and_advanced_scope_remains_deferred() -> None:
  plan = PLAN.read_text(encoding="utf-8")

  assert "iOS 核心契约迁移已交付" in plan
  assert "高级诊断与实时体验仍待 macOS" in plan

  ios_sources = [
    path.read_text(encoding="utf-8")
    for path in IOS_ROOT.rglob("*")
    if path.is_file() and path.suffix in {".swift", ".graphql"}
  ]
  combined = "\n".join(ios_sources)
  assert "tTradeSignalHistoryPage" not in combined
  for symbol in CORE_V3_IOS_SYMBOLS:
    assert symbol in combined
  for symbol in DEFERRED_V3_IOS_SYMBOLS:
    assert symbol not in combined
