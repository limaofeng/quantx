import XCTest

@testable import QuantX

@MainActor
final class MarketRepositoryMutationTests: XCTestCase {
  func testWriteAccountRequiresOneExplicitMatchingAccount() {
    XCTAssertNoThrow(
      try MarketRepository.validateWriteAccount(
        "ACCOUNT-1",
        authorizedAccountIDs: ["ACCOUNT-1"]
      )
    )
    XCTAssertThrowsError(
      try MarketRepository.validateWriteAccount(
        "ACCOUNT-1",
        authorizedAccountIDs: ["ACCOUNT-1", "ACCOUNT-2"]
      )
    ) { error in
      XCTAssertEqual(error as? WatchlistMutationError, .accountScopeMismatch)
    }
    XCTAssertThrowsError(
      try MarketRepository.validateWriteAccount(
        "ACCOUNT-2",
        authorizedAccountIDs: ["ACCOUNT-1"]
      )
    ) { error in
      XCTAssertEqual(error as? WatchlistMutationError, .accountScopeMismatch)
    }
  }

  func testStockCodeValidationNormalizesCanonicalAShareCodesAndRejectsDuplicates() throws {
    XCTAssertEqual(
      try MarketRepository.normalizedAStockCode(" 600519.sh "),
      "600519.SH"
    )
    XCTAssertThrowsError(try MarketRepository.normalizedAStockCode("600519"))
    XCTAssertThrowsError(
      try MarketRepository.validateReorderStockCodes([
        "600519.SH", "600519.sh",
      ])
    ) { error in
      XCTAssertEqual(
        error as? WatchlistMutationError,
        .invalidRequest("自选排序不能包含重复证券")
      )
    }
  }

  func testMutationMappingRejectsServerAccountOrStockSubstitution() {
    XCTAssertThrowsError(
      try makeItem(accountID: "ACCOUNT-2")
    ) { error in
      XCTAssertEqual(error as? WatchlistMutationError, .accountScopeMismatch)
    }
    XCTAssertThrowsError(
      try makeItem(stockCode: "000001.SZ")
    ) { error in
      XCTAssertEqual(error as? WatchlistMutationError, .contextMismatch)
    }
  }

  func testAuthoritativeReorderPreservesServerSequenceAndRequiresExactSet() throws {
    let serverItems = [
      try makeItem(stockCode: "000001.SZ", expectedStockCode: nil, displayOrder: 1),
      try makeItem(stockCode: "600519.SH", expectedStockCode: nil, displayOrder: 2),
    ]

    let validated = try MarketRepository.validateAuthoritativeReorder(
      serverItems,
      requestedStockCodes: ["600519.SH", "000001.SZ"]
    )

    XCTAssertEqual(validated.map(\.stockCode), ["000001.SZ", "600519.SH"])
    XCTAssertThrowsError(
      try MarketRepository.validateAuthoritativeReorder(
        serverItems,
        requestedStockCodes: ["600519.SH", "300750.SZ"]
      )
    ) { error in
      XCTAssertEqual(error as? WatchlistMutationError, .contextMismatch)
    }
  }

  func testAuthoritativeItemReorderRequiresExactServerItemIDs() throws {
    let serverItems = [
      try makeItem(
        id: "watchlist-1",
        stockCode: "000001.SZ",
        expectedStockCode: nil,
        displayOrder: 1
      ),
      try makeItem(
        id: "watchlist-2",
        stockCode: "600519.SH",
        expectedStockCode: nil,
        displayOrder: 2
      ),
    ]

    XCTAssertNoThrow(
      try MarketRepository.validateAuthoritativeReorder(
        serverItems,
        requestedItemIDs: serverItems.map(\.id)
      )
    )
    XCTAssertThrowsError(
      try MarketRepository.validateAuthoritativeReorder(
        serverItems,
        requestedItemIDs: ["item-1", "item-2"]
      )
    ) { error in
      XCTAssertEqual(error as? WatchlistMutationError, .contextMismatch)
    }
  }

  private func makeItem(
    id: String = "watchlist-1",
    accountID: String = "ACCOUNT-1",
    stockCode: String = "600519.SH",
    expectedStockCode: String? = "600519.SH",
    displayOrder: Int = 1
  ) throws -> MarketWatchItem {
    try MarketRepository.mapMutationItem(
      id: id,
      accountID: accountID,
      stockCode: stockCode,
      instrumentName: "证券",
      displayOrder: displayOrder,
      note: nil,
      updatedAt: "2026-08-15T01:00:00Z",
      expectedAccountID: "ACCOUNT-1",
      expectedStockCode: expectedStockCode
    )
  }
}
