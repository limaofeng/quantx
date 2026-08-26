from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IOS_ROOT = ROOT / "apps" / "ios"
PLAN = ROOT / "docs" / "plans" / "持仓做T有状态机会引擎V3实施规格.md"

V3_IOS_SYMBOLS = (
  "IOSTTradeSignalEvaluationsQuery",
  "IOSTTradeSignalDiagnosticsQuery",
  "IOSTTradeCandidateTraceQuery",
  "IOSTTradeUpdatesSubscription",
  "IOSRecordTTradeClientTelemetryMutation",
  "IOSTTradeSignalSnapshotFields",
  "TTradeCandidateApprovalExpectationInput",
  "TTradeSignalEvaluationKind",
  "TTradeOpportunitySnapshot",
  "TTradeCandidateTrace",
  "TTradeClientTelemetry",
)


def test_v3_ios_scope_is_explicitly_waived_on_windows() -> None:
  plan = PLAN.read_text(encoding="utf-8")

  assert "Windows 当前交付已明确 iOS scope-waiver" in plan
  assert "§16、§18.6 与 Phase 4 保留为后续 iOS 计划" in plan
  assert "Web 只按桌面体验验收" in plan
  assert "移动 Web" in plan

  ios_sources = [
    path.read_text(encoding="utf-8")
    for path in IOS_ROOT.rglob("*")
    if path.is_file() and path.suffix in {".swift", ".graphql"}
  ]
  combined = "\n".join(ios_sources)
  for symbol in V3_IOS_SYMBOLS:
    assert symbol not in combined
