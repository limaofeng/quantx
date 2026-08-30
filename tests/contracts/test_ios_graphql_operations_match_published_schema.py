from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IOS_OPERATIONS = ROOT / "apps" / "ios" / "QuantX" / "GraphQL" / "Operations"
PLAN = ROOT / "docs" / "plans" / "持仓做T有状态机会引擎V3实施规格.md"

CORE_V3_IOS_CONTRACT = (
  "signalSnapshot {",
  "candidateFingerprint",
  "candidateStateVersion",
  "stateSchemaVersion",
  "featureSchemaVersion",
  "$expectation: TTradeCandidateApprovalExpectationInput!",
  "filter: { scope: CURRENT }",
)

DEFERRED_V3_IOS_OPERATIONS = (
  "IOSTTradeSignalEvaluations",
  "IOSTTradeSignalDiagnostics",
  "IOSTTradeCandidateTrace",
  "IOSTTradeUpdates",
  "IOSRecordTTradeClientTelemetry",
  "IOSTTradeSignalSnapshotFields",
  "TTradeSignalEvaluationKind",
)


def test_ios_graphql_core_v3_contract_is_delivered_without_advanced_operations() -> None:
  plan = PLAN.read_text(encoding="utf-8")
  assert "iOS 核心契约迁移已交付" in plan
  assert "高级诊断与实时体验仍待 macOS" in plan

  operation_sources = [
    path.read_text(encoding="utf-8")
    for path in sorted(IOS_OPERATIONS.rglob("*.graphql"))
  ]
  combined = "\n".join(operation_sources)
  assert "tTradeSignalHistoryPage" not in combined
  for operation in CORE_V3_IOS_CONTRACT:
    assert operation in combined
  for operation in DEFERRED_V3_IOS_OPERATIONS:
    assert operation not in combined
