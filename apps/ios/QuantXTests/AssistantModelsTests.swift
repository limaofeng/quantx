import Foundation
import XCTest

@testable import QuantX

final class AssistantModelsTests: XCTestCase {
  func testLimitUpStrategyClassificationRecognizesSupportedNames() {
    XCTAssertTrue(makeStrategy(strategyKey: "limit_up_board").isLimitUpBoardStrategy)
    XCTAssertTrue(makeStrategy(strategyName: "A 股打板策略").isLimitUpBoardStrategy)
    XCTAssertTrue(makeStrategy(displayName: "Limit-Up 监控").isLimitUpBoardStrategy)
  }

  func testLimitUpStrategyClassificationDoesNotTreatUnrelatedStrategyAsBoardStrategy() {
    XCTAssertFalse(
      makeStrategy(
        strategyKey: "dynamic_balance",
        strategyName: "动态天平",
        displayName: "平安银行核心仓"
      ).isLimitUpBoardStrategy
    )
  }

  func testReadOnlyGraphQLErrorKeepsSafeCodeAndRequestID() {
    let error = ReadOnlyRepositoryError.graphQL(
      code: "INTERNAL_SERVER_ERROR",
      requestID: "req-safe-diagnostic"
    )

    XCTAssertEqual(
      error.errorDescription,
      "服务端拒绝了数据请求（INTERNAL_SERVER_ERROR），请求 ID：req-safe-diagnostic"
    )
  }

  func testAssistantNumberValidationRejectsNonFiniteValues() {
    XCTAssertThrowsError(
      try ReadOnlyModelValidator.requireFinite(
        [10.5, .nan],
        field: "assistant.price"
      )
    ) { error in
      XCTAssertEqual(error as? ReadOnlyMappingError, .invalidField("assistant.price"))
    }
  }

  private func makeStrategy(
    strategyKey: String = "strategy",
    strategyName: String? = nil,
    displayName: String = "策略实例"
  ) -> StrategyMonitorItem {
    StrategyMonitorItem(
      id: "run-id",
      strategyKey: strategyKey,
      strategyID: 1,
      strategyName: strategyName,
      instrumentCode: "000001.SZ",
      displayName: displayName,
      status: "RUNNING",
      mode: "PAPER",
      parameterVersion: "v1",
      createdAt: Date(timeIntervalSince1970: 1),
      updatedAt: Date(timeIntervalSince1970: 2),
      lastDecisionAt: nil,
      latestExecutionStatus: nil
    )
  }
}
