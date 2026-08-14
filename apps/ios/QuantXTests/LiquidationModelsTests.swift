import XCTest

@testable import QuantX

final class LiquidationModelsTests: XCTestCase {
  func testPaperIsTheFirstAndDefaultExecutionChoice() {
    XCTAssertEqual(LiquidationExecutionMode.allCases.first, .paper)
    XCTAssertEqual(LiquidationExecutionMode.paper.rawValue, "PAPER")
    XCTAssertEqual(LiquidationExecutionMode.live.rawValue, "LIVE")
  }

  func testUntilSnapshotClearedDoesNotClaimImmediateSale() {
    let detail = LiquidationCompletionStrategy.untilSnapshotCleared.detail

    XCTAssertTrue(detail.contains("后续"))
    XCTAssertTrue(detail.contains("风控校验"))
    XCTAssertFalse(detail.contains("立即卖出"))
    XCTAssertFalse(detail.contains("全部成交"))
  }

  func testCommandStatusKeepsUnknownServerValueConservative() {
    let status = LiquidationCommandStatus(serverValue: "new_engine_state")

    XCTAssertEqual(status, .unknown("NEW_ENGINE_STATE"))
    XCTAssertTrue(status.allowsRecovery)
    XCTAssertEqual(status.title, "Engine 返回未知状态")
  }

  func testQueuedAndProcessingMessagesNeverClaimPlanOrTradeSuccess() {
    for status in [LiquidationCommandStatus.pending, .processing] {
      let confirmation = makeConfirmation(status: status)

      XCTAssertTrue(confirmation.outcomeMessage.contains("尚未返回"))
      XCTAssertFalse(confirmation.outcomeMessage.contains("已创建。"))
      XCTAssertFalse(confirmation.outcomeMessage.contains("已成交"))
      XCTAssertFalse(confirmation.outcomeMessage.contains("券商已受理"))
    }
  }

  func testPartialMessageDoesNotPresentTheGroupAsFullySuccessful() {
    let confirmation = LiquidationConfirmation(
      success: false,
      code: "LIQUIDATION_PARTIAL",
      message: "server-message",
      challengeID: "challenge-1",
      groupID: "group-1",
      commandID: "command-1",
      status: .succeeded,
      createdCount: 1,
      failedCount: 1,
      plans: []
    )

    XCTAssertTrue(confirmation.isPartial)
    XCTAssertTrue(confirmation.outcomeMessage.contains("部分计划创建结果"))
    XCTAssertTrue(confirmation.outcomeMessage.contains("1 个未创建"))
    XCTAssertFalse(confirmation.outcomeMessage.contains("全部"))
  }

  func testInstrumentCodeRequiresExplicitAShareMarketSuffix() throws {
    XCTAssertEqual(
      try LiquidationDomainValidator.canonicalInstrumentCode(" 600519.sh "),
      "600519.SH"
    )
    XCTAssertThrowsError(
      try LiquidationDomainValidator.canonicalInstrumentCode("600519")
    )
    XCTAssertThrowsError(
      try LiquidationDomainValidator.canonicalInstrumentCode("AAPL.US")
    )
  }

  private func makeConfirmation(
    status: LiquidationCommandStatus
  ) -> LiquidationConfirmation {
    LiquidationConfirmation(
      success: true,
      code: "LIQUIDATION_QUEUED",
      message: "server-message",
      challengeID: "challenge-1",
      groupID: "group-1",
      commandID: "command-1",
      status: status,
      createdCount: 0,
      failedCount: 0,
      plans: []
    )
  }
}
