import Foundation
import XCTest

@testable import QuantX

@MainActor
final class PushNotificationRepositoryTests: XCTestCase {
  func testRegistrationMappingRequiresExactMetadataAndAllFiveServerPreferences() throws {
    let snapshot = try PushNotificationRepository.mapRegistration(
      id: UUID().uuidString,
      deviceInstallID: Self.metadata.installationIDValue,
      appBundleID: Self.metadata.runtime.appBundleID,
      appVersion: Self.metadata.runtime.appVersion,
      environment: .sandbox,
      registeredAt: "2026-08-15T00:00:00Z",
      updatedAt: "2026-08-15T00:01:00Z",
      preferences: [
        (.actionRequired, true),
        (.orderUpdate, true),
        (.riskSafety, true),
        (.automationError, true),
        (.connectionData, false),
      ],
      expectedMetadata: Self.metadata
    )

    XCTAssertEqual(snapshot.metadata, Self.metadata)
    XCTAssertEqual(snapshot.preferences, PushNotificationCategory.defaultPreferences)
  }

  func testRegistrationMappingRejectsMissingDuplicateAndUnknownPreferences() {
    let base: [(QuantXAPI.PushCategory?, Bool)] = [
      (.actionRequired, true),
      (.orderUpdate, true),
      (.riskSafety, true),
      (.automationError, true),
      (.connectionData, false),
    ]
    assertRegistrationRejected(Array(base.dropLast()))
    assertRegistrationRejected(base + [(.connectionData, true)])
    assertRegistrationRejected(Array(base.dropLast()) + [(nil, false)])
  }

  func testRegistrationMappingRejectsEnvironmentOrInstallationMismatch() {
    XCTAssertThrowsError(
      try PushNotificationRepository.mapRegistration(
        id: UUID().uuidString,
        deviceInstallID: UUID().uuidString,
        appBundleID: Self.metadata.runtime.appBundleID,
        appVersion: Self.metadata.runtime.appVersion,
        environment: .production,
        registeredAt: "2026-08-15T00:00:00Z",
        updatedAt: "2026-08-15T00:01:00Z",
        preferences: completePreferences,
        expectedMetadata: Self.metadata
      )
    )
  }

  func testRouteMappingRequiresRequestedUUIDAndKnownEnums() throws {
    let eventID = UUID()
    let route = try PushNotificationRepository.mapRoute(
      eventID: eventID.uuidString,
      category: .riskSafety,
      route: .tradingSafety,
      occurredAt: "2026-08-15T00:00:00Z",
      expiresAt: "2026-08-15T00:05:00Z",
      expired: false,
      expectedEventID: eventID
    )

    XCTAssertEqual(route.eventID, eventID)
    XCTAssertEqual(route.category, .riskSafety)
    XCTAssertEqual(route.route, .tradingSafety)

    XCTAssertThrowsError(
      try PushNotificationRepository.mapRoute(
        eventID: UUID().uuidString,
        category: .riskSafety,
        route: .tradingSafety,
        occurredAt: "2026-08-15T00:00:00Z",
        expiresAt: "2026-08-15T00:05:00Z",
        expired: false,
        expectedEventID: eventID
      )
    )
    XCTAssertThrowsError(
      try PushNotificationRepository.mapRoute(
        eventID: eventID.uuidString,
        category: nil,
        route: .tradingSafety,
        occurredAt: "2026-08-15T00:00:00Z",
        expiresAt: "2026-08-15T00:05:00Z",
        expired: false,
        expectedEventID: eventID
      )
    )
  }

  private func assertRegistrationRejected(
    _ preferences: [(QuantXAPI.PushCategory?, Bool)],
    file: StaticString = #filePath,
    line: UInt = #line
  ) {
    XCTAssertThrowsError(
      try PushNotificationRepository.mapRegistration(
        id: UUID().uuidString,
        deviceInstallID: Self.metadata.installationIDValue,
        appBundleID: Self.metadata.runtime.appBundleID,
        appVersion: Self.metadata.runtime.appVersion,
        environment: .sandbox,
        registeredAt: "2026-08-15T00:00:00Z",
        updatedAt: "2026-08-15T00:01:00Z",
        preferences: preferences,
        expectedMetadata: Self.metadata
      ),
      file: file,
      line: line
    )
  }

  private var completePreferences: [(QuantXAPI.PushCategory?, Bool)] {
    [
      (.actionRequired, true),
      (.orderUpdate, true),
      (.riskSafety, true),
      (.automationError, true),
      (.connectionData, false),
    ]
  }

  private static let metadata = PushDeviceMetadata(
    installationID: UUID(uuidString: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")!,
    runtime: PushNotificationRuntimeConfiguration(
      appBundleID: "com.limaofeng.quantx",
      appVersion: "1.0 (1)",
      environment: .sandbox
    )
  )
}
