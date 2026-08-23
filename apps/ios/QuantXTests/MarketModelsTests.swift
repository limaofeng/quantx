import XCTest

@testable import QuantX

final class MarketModelsTests: XCTestCase {
  func testQuoteMappingPreservesServerValuesAndAshareTrend() throws {
    let quote = try MarketMapping.quote(
      stockCode: "600519.SH",
      time: "2026-08-14T06:59:58Z",
      lastPrice: 1_432.50,
      open: 1_420,
      high: 1_438,
      low: 1_416,
      preClose: 1_419.20,
      change: 13.30,
      changePercent: 0.94,
      volume: 12_300,
      amount: 17_600_000,
      turnoverRate: 0.21
    )

    XCTAssertEqual(quote.stockCode, "600519.SH")
    XCTAssertEqual(quote.lastPrice, 1_432.50)
    XCTAssertEqual(quote.trend, 13.30, accuracy: 0.001)
    XCTAssertEqual(quote.changePercent, 0.94)
  }

  func testQuoteMappingRejectsNonFiniteOrNegativeMarketFacts() {
    XCTAssertThrowsError(
      try MarketMapping.quote(
        stockCode: "000001.SZ",
        time: "2026-08-14T06:59:58Z",
        lastPrice: .infinity,
        open: 10,
        high: 10,
        low: 10,
        preClose: 10,
        change: nil,
        changePercent: nil,
        volume: 1,
        amount: 1,
        turnoverRate: nil
      )
    )

    XCTAssertThrowsError(
      try MarketMapping.quote(
        stockCode: "000001.SZ",
        time: "2026-08-14T06:59:58Z",
        lastPrice: 10,
        open: 10,
        high: 10,
        low: 10,
        preClose: 10,
        change: nil,
        changePercent: nil,
        volume: -1,
        amount: 1,
        turnoverRate: nil
      )
    )
  }

  func testInstrumentKeepsCanonicalStockCodeSeparateFromLocalContractID() throws {
    let instrument = try MarketMapping.instrument(
      stockCode: "600519.SH",
      market: "SH",
      instrumentID: "600519",
      name: "贵州茅台",
      abbreviation: "GZMT",
      exchangeCode: "SH",
      previousClose: 1_420,
      upperLimit: 1_562,
      lowerLimit: 1_278,
      priceTick: 0.01,
      isTrading: true,
      quote: nil
    )

    XCTAssertEqual(instrument.id, "600519.SH")
    XCTAssertEqual(instrument.stockCode, "600519.SH")
    XCTAssertEqual(instrument.instrumentID, "600519")
  }

  func testWorkspaceFreshnessUsesOldestRealQuote() throws {
    let older = try XCTUnwrap(PortfolioDateParser.parse("2026-08-14T06:59:50Z"))
    let newer = try XCTUnwrap(PortfolioDateParser.parse("2026-08-14T06:59:58Z"))
    let base = MarketQuote(
      stockCode: "600000.SH",
      time: older,
      lastPrice: 10,
      open: 10,
      high: 10,
      low: 10,
      preClose: 10,
      change: 0,
      changePercent: 0,
      volume: 0,
      amount: 0,
      turnoverRate: nil
    )
    let second = MarketQuote(
      stockCode: "000001.SZ",
      time: newer,
      lastPrice: 11,
      open: 11,
      high: 11,
      low: 11,
      preClose: 11,
      change: 0,
      changePercent: 0,
      volume: 0,
      amount: 0,
      turnoverRate: nil
    )
    let snapshot = MarketWorkspaceSnapshot(
      accountID: "account-id",
      watchlist: [
        watchItem(id: "1", code: base.stockCode, quote: base),
        watchItem(id: "2", code: second.stockCode, quote: second),
      ],
      fetchedAt: Date()
    )

    XCTAssertEqual(snapshot.sourceUpdatedAt, older)
  }

  func testPeriodsMapOnlyToPublishedGraphQLEnums() {
    XCTAssertEqual(MarketPeriod.minute.graphQLValue, .min1)
    XCTAssertEqual(MarketPeriod.fiveMinutes.graphQLValue, .min5)
    XCTAssertEqual(MarketPeriod.day.graphQLValue, .day1)
    XCTAssertEqual(MarketPeriod.week.graphQLValue, .week1)
  }

  private func watchItem(
    id: String,
    code: String,
    quote: MarketQuote
  ) -> MarketWatchItem {
    MarketWatchItem(
      id: id,
      accountID: "account-id",
      stockCode: code,
      instrumentName: nil,
      displayOrder: Int(id) ?? 0,
      note: nil,
      updatedAt: nil,
      quote: quote
    )
  }
}
