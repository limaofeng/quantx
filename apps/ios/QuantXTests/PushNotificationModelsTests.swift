import Foundation
import XCTest

@testable import QuantX

final class PushNotificationModelsTests: XCTestCase {
  func testDeviceTokenEncodingDoesNotAssumeAppleTokenLength() {
    XCTAssertEqual(APNsDeviceTokenEncoder.lowercaseHex(Data([0x00, 0x01, 0xfe, 0xff])), "0001feff")
    XCTAssertEqual(
      APNsDeviceTokenEncoder.lowercaseHex(Data((0..<4_097).map { UInt8($0 % 256) })).count,
      8_194
    )
    XCTAssertEqual(APNsDeviceTokenEncoder.lowercaseHex(Data()), "")
  }

  func testPayloadParserAcceptsOnlyOpaqueEventAndAllowlistedMetadata() throws {
    let eventID = UUID()
    let parsed = try PushNotificationPayloadParser.eventID(
      from: [
        "aps": ["alert": ["title": "状态更新"]],
        "eventId": eventID.uuidString.lowercased(),
        "category": "ORDER_UPDATE",
        "route": "trading.orders",
        "accountId": "must-not-be-trusted",
        "instrumentCode": "must-not-be-trusted",
      ]
    )

    XCTAssertEqual(parsed, eventID)
  }

  func testPayloadParserRejectsInvalidUUIDCategoryAndRoute() {
    assertRejected(
      ["eventId": "order-42", "category": "ORDER_UPDATE", "route": "trading.orders"],
      expected: .invalidEventID
    )
    assertRejected(
      ["eventId": UUID().uuidString, "category": "ACCOUNT_BALANCE", "route": "today.action"],
      expected: .invalidCategory
    )
    assertRejected(
      ["eventId": UUID().uuidString, "category": "ORDER_UPDATE", "route": "trade/confirm"],
      expected: .invalidRoute
    )
  }

  func testCategoryDefaultsKeepConnectionDataOff() {
    XCTAssertEqual(
      Set(PushNotificationCategory.defaultPreferences.keys),
      Set(PushNotificationCategory.allCases)
    )
    XCTAssertEqual(PushNotificationCategory.defaultPreferences[.connectionData], false)
    XCTAssertTrue(
      PushNotificationCategory.allCases
        .filter { $0 != .connectionData }
        .allSatisfy { PushNotificationCategory.defaultPreferences[$0] == true }
    )
  }

  func testRouteDestinationsAreTypeSafeAndNeverRepresentTradeActions() {
    XCTAssertEqual(NotificationRouteType.todayAction.destination, .today)
    XCTAssertEqual(NotificationRouteType.tradingOrders.destination, .tradingOrders)
    XCTAssertEqual(NotificationRouteType.tradingSafety.destination, .tradingSafety)
    XCTAssertEqual(NotificationRouteType.quantWorkspace.destination, .quant)
    XCTAssertEqual(NotificationRouteType.systemStatus.destination, .systemStatus)
  }

  private func assertRejected(
    _ payload: [AnyHashable: Any],
    expected: PushNotificationPayloadParser.ParseError,
    file: StaticString = #filePath,
    line: UInt = #line
  ) {
    XCTAssertThrowsError(
      try PushNotificationPayloadParser.eventID(from: payload),
      file: file,
      line: line
    ) { error in
      XCTAssertEqual(error as? PushNotificationPayloadParser.ParseError, expected)
    }
  }
}
