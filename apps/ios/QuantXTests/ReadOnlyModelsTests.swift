@_spi(Unsafe) import ApolloAPI
import Foundation
import XCTest

@testable import QuantX

final class ReadOnlyModelsTests: XCTestCase {
  func testMapsStrategyInstanceAndKeepsUnknownStatusVisible() throws {
    let graphQL = QuantXAPI.IOSStrategyInstancesQuery.Data.StrategyInstance(
      _dataDict: DataDict(
        data: [
          "__typename": "StrategyInstance",
          "id": "instance-1",
          "strategyKey": "ashare_supermarket",
          "strategyName": "A 股超市策略",
          "instrumentCode": "000001.SZ",
          "displayName": "平安银行监控",
          "status": GraphQLEnum<QuantXAPI.StrategyRunStatus>(rawValue: "RECOVERING"),
          "mode": GraphQLEnum(QuantXAPI.StrategyRunMode.paper),
          "parameterVersion": "v3",
          "createdAt": "2026-08-10T01:00:00Z",
          "updatedAt": "2026-08-10T02:00:00Z",
        ],
        fulfilledFragments: [
          ObjectIdentifier(QuantXAPI.IOSStrategyInstancesQuery.Data.StrategyInstance.self)
        ]
      )
    )

    let instance = try StrategyMonitorItem(graphQL: graphQL)

    XCTAssertEqual(instance.status, "RECOVERING")
    XCTAssertEqual(instance.statusDisplayName, "未知（RECOVERING）")
    XCTAssertEqual(instance.modeDisplayName, "模拟盘")
    XCTAssertEqual(instance.instrumentCode, "000001.SZ")
  }

  func testOrderKeepsPartialFillDistinctFromCompleted() throws {
    let graphQL = makeTodayOrder(
      status: .partSucc,
      volume: 1_000,
      tradedVolume: 400
    )

    let order = try OrderRecord(graphQL: graphQL)

    XCTAssertEqual(order.statusDisplayName, "部分成交")
    XCTAssertEqual(order.remainingVolume, 600)
    XCTAssertEqual(order.sideDisplayName, "买入")
  }

  func testOrderRejectsTradedVolumeGreaterThanOrderVolume() {
    let graphQL = makeTodayOrder(
      status: .succeeded,
      volume: 100,
      tradedVolume: 200
    )

    XCTAssertThrowsError(try OrderRecord(graphQL: graphQL)) { error in
      XCTAssertEqual(
        error as? ReadOnlyMappingError,
        .invalidField("order.tradedVolume")
      )
    }
  }

  func testTradeParsesMillisecondTimestampAndBuyDirection() throws {
    let graphQL = makeTodayTrade(orderType: 23, tradedTime: 1_786_317_600_000)

    let trade = try TradeRecord(graphQL: graphQL)

    XCTAssertEqual(trade.sideDisplayName, "买入")
    let executedAt = try XCTUnwrap(trade.executedAt)
    XCTAssertEqual(executedAt.timeIntervalSince1970, 1_786_317_600, accuracy: 0.001)
  }

  func testTradeDoesNotInventSellDirectionForUnknownBrokerCode() throws {
    let graphQL = makeTodayTrade(orderType: 99, tradedTime: 1_786_317_600)

    let trade = try TradeRecord(graphQL: graphQL)

    XCTAssertEqual(trade.sideDisplayName, "未知方向（99）")
  }

  private func makeTodayOrder(
    status: QuantXAPI.OrderStatus,
    volume: Int,
    tradedVolume: Int
  ) -> QuantXAPI.IOSTodayOrdersQuery.Data.TodayOrder {
    QuantXAPI.IOSTodayOrdersQuery.Data.TodayOrder(
      _dataDict: DataDict(
        data: [
          "__typename": "Order",
          "id": "order-1",
          "sysid": "broker-order-1",
          "stockCode": "000001.SZ",
          "stockName": "平安银行",
          "type": GraphQLEnum(QuantXAPI.OrderType.buy),
          "status": GraphQLEnum(status),
          "price": 10.5,
          "volume": volume,
          "tradedVolume": tradedVolume,
          "tradedPrice": tradedVolume > 0 ? 10.48 : 0,
          "time": "2026-08-10T02:00:00Z",
        ],
        fulfilledFragments: [
          ObjectIdentifier(QuantXAPI.IOSTodayOrdersQuery.Data.TodayOrder.self)
        ]
      )
    )
  }

  private func makeTodayTrade(
    orderType: Int,
    tradedTime: Int
  ) -> QuantXAPI.IOSTodayTradesQuery.Data.TodayTrade {
    QuantXAPI.IOSTodayTradesQuery.Data.TodayTrade(
      _dataDict: DataDict(
        data: [
          "__typename": "Trade",
          "accountId": "authorized-account",
          "tradedId": "trade-1",
          "orderId": 101,
          "orderSysid": "broker-order-1",
          "stockCode": "000001.SZ",
          "stockName": "平安银行",
          "orderType": orderType,
          "tradedPrice": 10.48,
          "tradedVolume": 400,
          "tradedAmount": 4_192.0,
          "tradedTime": tradedTime,
        ],
        fulfilledFragments: [
          ObjectIdentifier(QuantXAPI.IOSTodayTradesQuery.Data.TodayTrade.self)
        ]
      )
    )
  }
}
