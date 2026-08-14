@_spi(Unsafe) import ApolloAPI
import Foundation
import XCTest

@testable import QuantX

final class PortfolioModelsTests: XCTestCase {
  func testParsesLegacyDatabaseTimestampAsAsiaShanghai() throws {
    let parsed = try XCTUnwrap(
      PortfolioDateParser.parse("2026-05-11T22:25:51.377371")
    )
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let expected = try XCTUnwrap(
      formatter.date(from: "2026-05-11T14:25:51.377Z")
    )

    XCTAssertEqual(parsed.timeIntervalSince1970, expected.timeIntervalSince1970, accuracy: 0.001)
  }

  func testMapsGeneratedAccountModelWithoutInventingValues() throws {
    let graphQL = QuantXAPI.IOSCurrentAccountQuery.Data.CurrentAccount(
      _dataDict: DataDict(
        data: [
          "__typename": "Account",
          "id": "authorized-account",
          "accountName": "只读账户",
          "accountType": "STOCK",
          "totalAsset": 123_456.78,
          "cash": 23_456.78,
          "frozenCash": 100.0,
          "marketValue": 100_000.0,
          "totalProfitLoss": 3_456.0,
          "profitLossPercent": 2.88,
          "updateTime": "2026-07-21T09:30:00.123Z",
        ],
        fulfilledFragments: [
          ObjectIdentifier(QuantXAPI.IOSCurrentAccountQuery.Data.CurrentAccount.self)
        ]
      )
    )

    let account = try PortfolioAccount(graphQL: graphQL)

    XCTAssertEqual(account.id, "authorized-account")
    XCTAssertEqual(account.totalAsset, 123_456.78)
    XCTAssertEqual(account.profitLossPercent, 2.88)
    XCTAssertNotNil(account.updatedAt)
  }

  func testMapsGeneratedPositionNullableFieldsAsMissing() throws {
    let graphQL = QuantXAPI.IOSPositionsQuery.Data.Position(
      _dataDict: DataDict(
        data: [
          "__typename": "Position",
          "id": "position-1",
          "accountId": "authorized-account",
          "stockCode": "000001.SZ",
          "volume": 1_000,
          "canUseVolume": 900,
        ],
        fulfilledFragments: [
          ObjectIdentifier(QuantXAPI.IOSPositionsQuery.Data.Position.self)
        ]
      )
    )

    let position = try PortfolioPosition(graphQL: graphQL)

    XCTAssertEqual(position.displayName, "000001.SZ")
    XCTAssertEqual(position.volume, 1_000)
    XCTAssertNil(position.lastPrice)
    XCTAssertNil(position.profitLoss)
    XCTAssertNil(position.updatedAt)
  }

  func testRejectsNonFiniteGraphQLFinancialValue() throws {
    let graphQL = QuantXAPI.IOSPortfolioSummaryQuery.Data.PortfolioSummary(
      _dataDict: DataDict(
        data: [
          "__typename": "PortfolioSummary",
          "accountId": "authorized-account",
          "accountName": "只读账户",
          "totalAsset": Double.infinity,
          "cash": 1.0,
          "totalMarketValue": 1.0,
          "totalProfitLoss": 0.0,
          "totalProfitLossPercent": 0.0,
          "positionCount": 0,
          "updateTime": "2026-07-21T09:30:00Z",
        ],
        fulfilledFragments: [
          ObjectIdentifier(QuantXAPI.IOSPortfolioSummaryQuery.Data.PortfolioSummary.self)
        ]
      )
    )

    XCTAssertThrowsError(try PortfolioMetrics(graphQL: graphQL)) { error in
      XCTAssertEqual(error as? PortfolioMappingError, .invalidField("summary.amount"))
    }
  }

  func testFreshnessUsesConservativeBoundaries() throws {
    let now = try XCTUnwrap(PortfolioDateParser.parse("2026-07-21T10:00:00Z"))

    XCTAssertEqual(
      DataFreshness.evaluate(updatedAt: now.addingTimeInterval(-90), now: now).level,
      .current
    )
    XCTAssertEqual(
      DataFreshness.evaluate(updatedAt: now.addingTimeInterval(-91), now: now).level,
      .delayed
    )
    XCTAssertEqual(
      DataFreshness.evaluate(updatedAt: now.addingTimeInterval(-301), now: now).level,
      .stale
    )
    XCTAssertEqual(DataFreshness.evaluate(updatedAt: nil, now: now).level, .unknown)
    XCTAssertEqual(
      DataFreshness.evaluate(updatedAt: now.addingTimeInterval(61), now: now).level,
      .unknown
    )
  }

  func testFinancialFormattersUseChineseMoneyAndExplicitDirection() {
    XCTAssertEqual(PortfolioFormatters.currency(1_234.5), "¥1,234.50")
    XCTAssertEqual(PortfolioFormatters.signedPercentage(2.5), "+2.50%")
    XCTAssertEqual(PortfolioFormatters.signedPercentage(-2.5), "-2.50%")
    XCTAssertEqual(PortfolioFormatters.percentage(35.5), "35.50%")
    XCTAssertEqual(PortfolioFormatters.currency(nil), "—")
  }

  func testEmptyPortfolioUsesOldestConfirmedSourceTimestamp() {
    let older = Date(timeIntervalSince1970: 1_800_000_000)
    let newer = older.addingTimeInterval(30)
    let snapshot = makeSnapshot(
      accountUpdatedAt: newer,
      metricsUpdatedAt: older,
      positions: []
    )

    XCTAssertEqual(snapshot.sourceUpdatedAt, older)
    XCTAssertFalse(snapshot.positionCountDoesNotMatch)
  }

  func testMissingSourceTimestampProducesUnknownFreshness() {
    let snapshot = makeSnapshot(
      accountUpdatedAt: Date(),
      metricsUpdatedAt: nil,
      positions: []
    )

    XCTAssertNil(snapshot.sourceUpdatedAt)
    XCTAssertEqual(
      DataFreshness.evaluate(updatedAt: snapshot.sourceUpdatedAt).level,
      .unknown
    )
  }

  func testPositionCountMismatchIsExplicitlyDetectable() {
    let snapshot = makeSnapshot(
      accountUpdatedAt: Date(),
      metricsUpdatedAt: Date(),
      positionCount: 1,
      positions: []
    )

    XCTAssertTrue(snapshot.positionCountDoesNotMatch)
  }

  private func makeSnapshot(
    accountUpdatedAt: Date?,
    metricsUpdatedAt: Date?,
    positionCount: Int = 0,
    positions: [PortfolioPosition]
  ) -> PortfolioSnapshot {
    PortfolioSnapshot(
      account: PortfolioAccount(
        id: "account-id",
        name: "只读账户",
        type: "STOCK",
        totalAsset: 0,
        cash: 0,
        frozenCash: 0,
        marketValue: 0,
        totalProfitLoss: nil,
        profitLossPercent: nil,
        updatedAt: accountUpdatedAt
      ),
      metrics: PortfolioMetrics(
        accountID: "account-id",
        accountName: "只读账户",
        totalAsset: 0,
        cash: 0,
        marketValue: 0,
        totalProfitLoss: 0,
        totalProfitLossPercent: 0,
        todayProfitLoss: nil,
        todayProfitLossPercent: nil,
        positionCount: positionCount,
        updatedAt: metricsUpdatedAt
      ),
      positions: positions,
      fetchedAt: Date()
    )
  }
}
