from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IOS_OPERATIONS = ROOT / "apps" / "ios" / "QuantX" / "GraphQL" / "Operations"
PLAN = ROOT / "docs" / "plans" / "持仓做T有状态机会引擎V3实施规格.md"

V3_IOS_OPERATIONS = (
  "IOSTTradeSignalEvaluations",
  "IOSTTradeSignalDiagnostics",
  "IOSTTradeCandidateTrace",
  "IOSTTradeUpdates",
  "IOSRecordTTradeClientTelemetry",
  "IOSTTradeSignalSnapshotFields",
  "TTradeCandidateApprovalExpectationInput",
  "TTradeSignalEvaluationKind",
)


def test_ios_graphql_v3_operations_are_scope_waived_on_windows() -> None:
  plan = PLAN.read_text(encoding="utf-8")
  assert "Windows 当前交付已明确 iOS scope-waiver" in plan
  assert "§16、§18.6 与 Phase 4 保留为后续 iOS 计划" in plan

  operation_sources = [
    path.read_text(encoding="utf-8")
    for path in sorted(IOS_OPERATIONS.rglob("*.graphql"))
  ]
  combined = "\n".join(operation_sources)
  for operation in V3_IOS_OPERATIONS:
    assert operation not in combined
