import SwiftUI
import UIKit
import UserNotifications

@MainActor
protocol PushNotificationSystemManaging: AnyObject {
  func authorizationStatus() async -> PushAuthorizationStatus
  func requestAuthorization() async throws -> Bool
  func registerForRemoteNotifications()
  func unregisterForRemoteNotifications()
  func openNotificationSettings() async
}

@MainActor
final class ApplePushNotificationSystem: PushNotificationSystemManaging {
  private let center: UNUserNotificationCenter

  init(center: UNUserNotificationCenter = .current()) {
    self.center = center
  }

  func authorizationStatus() async -> PushAuthorizationStatus {
    let settings = await center.notificationSettings()
    switch settings.authorizationStatus {
    case .notDetermined: return PushAuthorizationStatus.notDetermined
    case .denied: return PushAuthorizationStatus.denied
    case .authorized: return PushAuthorizationStatus.authorized
    case .provisional: return PushAuthorizationStatus.provisional
    case .ephemeral: return PushAuthorizationStatus.ephemeral
    @unknown default: return PushAuthorizationStatus.unknown
    }
  }

  func requestAuthorization() async throws -> Bool {
    try await center.requestAuthorization(options: [.alert, .badge, .sound])
  }

  func registerForRemoteNotifications() {
    UIApplication.shared.registerForRemoteNotifications()
  }

  func unregisterForRemoteNotifications() {
    UIApplication.shared.unregisterForRemoteNotifications()
  }

  func openNotificationSettings() async {
    guard let url = URL(string: UIApplication.openNotificationSettingsURLString) else {
      return
    }
    await UIApplication.shared.open(url)
  }
}

@MainActor
final class PushNotificationBridge {
  static let shared = PushNotificationBridge()

  private weak var store: PushNotificationStore?
  private var bufferedToken: String?
  private var bufferedEventID: UUID?
  private var bufferedInvalidResponse = false

  private init() {}

  func bind(to store: PushNotificationStore) {
    self.store = store
    if let bufferedToken {
      self.bufferedToken = nil
      Task { await store.receive(deviceToken: bufferedToken) }
    }
    if let bufferedEventID {
      self.bufferedEventID = nil
      Task { await store.receive(notificationEventID: bufferedEventID) }
    } else if bufferedInvalidResponse {
      bufferedInvalidResponse = false
      store.receiveInvalidNotificationResponse()
    }
  }

  func receive(deviceToken: String) {
    guard let store else {
      bufferedToken = deviceToken
      return
    }
    Task { await store.receive(deviceToken: deviceToken) }
  }

  func receive(eventID: UUID) {
    guard let store else {
      bufferedEventID = eventID
      bufferedInvalidResponse = false
      return
    }
    Task { await store.receive(notificationEventID: eventID) }
  }

  func receiveInvalidResponse() {
    guard let store else {
      bufferedEventID = nil
      bufferedInvalidResponse = true
      return
    }
    store.receiveInvalidNotificationResponse()
  }

  func receiveRegistrationFailure() {
    store?.receiveRemoteRegistrationFailure()
  }

  func clearBufferedSensitiveState() {
    bufferedToken = nil
    bufferedEventID = nil
    bufferedInvalidResponse = false
  }
}

final class PushNotificationAppDelegate: NSObject, UIApplicationDelegate,
  UNUserNotificationCenterDelegate
{
  func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
  ) -> Bool {
    UNUserNotificationCenter.current().delegate = self
    return true
  }

  func application(
    _ application: UIApplication,
    didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
  ) {
    let encoded = APNsDeviceTokenEncoder.lowercaseHex(deviceToken)
    Task { @MainActor in
      PushNotificationBridge.shared.receive(deviceToken: encoded)
    }
  }

  func application(
    _ application: UIApplication,
    didFailToRegisterForRemoteNotificationsWithError error: any Error
  ) {
    Task { @MainActor in
      PushNotificationBridge.shared.receiveRegistrationFailure()
    }
  }

  nonisolated func userNotificationCenter(
    _ center: UNUserNotificationCenter,
    willPresent notification: UNNotification
  ) async -> UNNotificationPresentationOptions {
    [.banner, .list, .sound]
  }

  nonisolated func userNotificationCenter(
    _ center: UNUserNotificationCenter,
    didReceive response: UNNotificationResponse
  ) async {
    let userInfo = response.notification.request.content.userInfo
    do {
      let eventID = try PushNotificationPayloadParser.eventID(from: userInfo)
      await MainActor.run {
        PushNotificationBridge.shared.receive(eventID: eventID)
      }
    } catch {
      await MainActor.run {
        PushNotificationBridge.shared.receiveInvalidResponse()
      }
    }
  }
}
