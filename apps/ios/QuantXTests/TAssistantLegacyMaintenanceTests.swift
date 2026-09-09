@_spi(Internal) @_spi(Execution) import ApolloAPI
import Foundation
import XCTest

@testable import QuantX

final class TAssistantLegacyMaintenanceTests: XCTestCase {
  let scope = TAssistantLegacyScope(
    accountID: "account-1", configID: "head", runID: "legacy-run", headVersion: 1)
  let commandID = UUID().uuidString.lowercased()

  func context(account: String = "account-1", device: String = "device", epoch: UUID = UUID())
    -> TTradeControlRepositoryContext
  {
    .init(
      userID: "user", deviceSessionID: device, activeAccountID: account,
      authorizedAccountIDs: [account], sessionContextID: epoch)
  }

  func evidence(_ replace: [String: GraphQLJSON] = [:]) -> GraphQLJSON {
    var manifest: [String: GraphQLJSON] = [
      "schema": .string("legacy-t-obligations.v1"), "account_id": .string("account-1"),
      "config_id": .string("head"), "run_id": .string("legacy-run"), "head_version": .integer(1),
    ]
    for key in [
      "intents", "pending", "correlations", "commands", "runtime_events", "batches", "exit_plans",
      "retained_client_order_ids", "unsubmitted_intent_ids_for_review",
    ] {
      manifest[key] = .array([])
    }
    manifest.merge(replace, uniquingKeysWith: { _, new in new })
    return .init(object: [
      "inventory_operation_id": .string("legacy-inventory:\(commandID)"),
      "manifest_hash": .string(String(repeating: "a", count: 64)),
      "manifest": .init(object: manifest),
    ])
  }

  func testInventoryScopeAndObligationShapeFailClosed() throws {
    let inventory = try TAssistantLegacyInventory.validated(
      evidence(), scope: scope, commandID: commandID)
    XCTAssertEqual(inventory.scope, scope)
    for change: [String: GraphQLJSON] in [
      ["account_id": .string("other")], ["run_id": .string("other")],
      ["head_version": .integer(2)], ["head_version": .boolean(true)],
      ["schema": .string("future")], ["pending": .null],
    ] {
      XCTAssertThrowsError(
        try TAssistantLegacyInventory.validated(
          evidence(change), scope: scope, commandID: commandID))
    }
    XCTAssertThrowsError(
      try TAssistantLegacyInventory.validated(
        evidence(), scope: scope, commandID: UUID().uuidString.lowercased()))
  }

  func testTicketCannotSurviveDeviceAccountOrLocalEpochChange() throws {
    let original = context()
    let inventory = try TAssistantLegacyInventory.validated(
      evidence(), scope: scope, commandID: commandID)
    let ticket = TAssistantLegacyDrainTicket(
      challengeID: UUID().uuidString, token: "memory-only", expiresAt: Date(),
      inventory: inventory, windowStart: Date(), windowEnd: Date().addingTimeInterval(30),
      context: original)
    XCTAssertNoThrow(try ticket.validate(original))
    XCTAssertThrowsError(try ticket.validate(context()))
    XCTAssertThrowsError(
      try ticket.validate(context(account: "other", epoch: original.sessionContextID)))
    XCTAssertThrowsError(
      try ticket.validate(context(device: "other", epoch: original.sessionContextID)))
  }

  func testJSONScalarPreservesVersionOneFromWire() throws {
    let data = Data(#"{"head_version":1,"enabled":true}"#.utf8)
    let raw = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: NSNumber])
    XCTAssertEqual(
      try GraphQLJSON(_jsonValue: XCTUnwrap(raw["head_version"])), GraphQLJSON.integer(1))
    XCTAssertEqual(
      try GraphQLJSON(_jsonValue: XCTUnwrap(raw["enabled"])), GraphQLJSON.boolean(true))
  }
}
