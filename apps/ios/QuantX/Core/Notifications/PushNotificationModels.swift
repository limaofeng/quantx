import Foundation

enum PushAuthorizationStatus: Equatable, Sendable {
  case notDetermined
  case denied
  case authorized
  case provisional
  case ephemeral
  case unknown

  var permitsRemoteRegistration: Bool {
    switch self {
    case .authorized, .provisional, .ephemeral:
      true
    case .notDetermined, .denied, .unknown:
      false
    }
  }

  var title: String {
    switch self {
    case .notDetermined: "尚未启用"
    case .denied: "系统已关闭"
    case .authorized: "系统已允许"
    case .provisional: "临时送达"
    case .ephemeral: "本次允许"
    case .unknown: "系统状态未知"
    }
  }
}

enum PushNotificationEnvironment: String, Equatable, Sendable {
  case sandbox = "SANDBOX"
  case production = "PRODUCTION"
}

enum PushNotificationCategory: String, CaseIterable, Identifiable, Equatable, Sendable {
  case actionRequired = "ACTION_REQUIRED"
  case orderUpdate = "ORDER_UPDATE"
  case riskSafety = "RISK_SAFETY"
  case automationError = "AUTOMATION_ERROR"
  case connectionData = "CONNECTION_DATA"

  var id: String { rawValue }

  var title: String {
    switch self {
    case .actionRequired: "待处理事项"
    case .orderUpdate: "委托与成交"
    case .riskSafety: "交易安全"
    case .automationError: "量化异常"
    case .connectionData: "连接与数据"
    }
  }

  var detail: String {
    switch self {
    case .actionRequired: "需要您打开应用核对的行动项"
    case .orderUpdate: "委托、成交与券商终态变化"
    case .riskSafety: "熔断、退出计划和风险门禁异常"
    case .automationError: "策略、做T或自动化执行异常"
    case .connectionData: "设备连接和数据新鲜度异常（默认关闭）"
    }
  }

  static let defaultPreferences: [Self: Bool] = [
    .actionRequired: true,
    .orderUpdate: true,
    .riskSafety: true,
    .automationError: true,
    .connectionData: false,
  ]
}

enum NotificationRouteType: String, CaseIterable, Equatable, Sendable {
  case todayAction = "today.action"
  case tradingOrders = "trading.orders"
  case tradingSafety = "trading.safety"
  case quantWorkspace = "quant.workspace"
  case systemStatus = "system.status"

  var destination: NotificationNavigationDestination {
    switch self {
    case .todayAction: .today
    case .tradingOrders: .tradingOrders
    case .tradingSafety: .tradingSafety
    case .quantWorkspace: .quant
    case .systemStatus: .systemStatus
    }
  }
}

enum NotificationNavigationDestination: Equatable, Sendable {
  case today
  case tradingOrders
  case tradingSafety
  case quant
  case systemStatus
}

struct NotificationNavigationRequest: Identifiable, Equatable, Sendable {
  let id: UUID
  let eventID: UUID
  let destination: NotificationNavigationDestination

  init(
    id: UUID = UUID(),
    eventID: UUID,
    destination: NotificationNavigationDestination
  ) {
    self.id = id
    self.eventID = eventID
    self.destination = destination
  }
}

enum NotificationDeepLinkState: Equatable, Sendable {
  case idle
  case waitingForUnlock
  case resolving
  case unavailable(String)
}

enum PushRegistrationState: Equatable, Sendable {
  case idle
  case waitingForAuthorization
  case waitingForToken
  case waitingForSession
  case registering
  case registered(updatedAt: Date)
  case unavailable(String)
}

struct PushNotificationRuntimeConfiguration: Equatable, Sendable {
  let appBundleID: String
  let appVersion: String
  let environment: PushNotificationEnvironment

  static func load(bundle: Bundle = .main) -> Self? {
    guard
      let bundleID = bundle.bundleIdentifier?.trimmingCharacters(in: .whitespacesAndNewlines),
      let shortVersion = bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString")
        as? String,
      let build = bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String,
      let environmentValue = bundle.object(forInfoDictionaryKey: "QuantXAPNsEnvironment")
        as? String
    else {
      return nil
    }
    return validated(
      appBundleID: bundleID,
      shortVersion: shortVersion,
      build: build,
      environmentValue: environmentValue
    )
  }

  static func validated(
    appBundleID: String,
    shortVersion: String,
    build: String,
    environmentValue: String
  ) -> Self? {
    let bundleID = appBundleID.trimmingCharacters(in: .whitespacesAndNewlines)
    let version = "\(shortVersion) (\(build))"
    guard
      bundleID.range(
        of: #"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$"#,
        options: .regularExpression
      ) != nil,
      !shortVersion.isEmpty,
      !build.isEmpty,
      version.count <= 64
    else {
      return nil
    }
    let environment: PushNotificationEnvironment
    switch environmentValue.uppercased() {
    case "SANDBOX":
      environment = .sandbox
    case "PRODUCTION":
      environment = .production
    default:
      return nil
    }
    return Self(
      appBundleID: bundleID,
      appVersion: version,
      environment: environment
    )
  }
}

struct PushDeviceMetadata: Equatable, Sendable {
  let installationID: UUID
  let runtime: PushNotificationRuntimeConfiguration

  var installationIDValue: String { installationID.uuidString.lowercased() }
}

struct PushRegistrationSnapshot: Equatable, Sendable {
  let id: String
  let metadata: PushDeviceMetadata
  let registeredAt: Date
  let updatedAt: Date
  let preferences: [PushNotificationCategory: Bool]
}

struct NotificationRouteResolution: Equatable, Sendable {
  let eventID: UUID
  let category: PushNotificationCategory
  let route: NotificationRouteType
  let occurredAt: Date
  let expiresAt: Date
  let expired: Bool
}

enum APNsDeviceTokenEncoder {
  private static let hexadecimal = Array("0123456789abcdef".utf8)

  static func lowercaseHex(_ data: Data) -> String {
    var encoded = [UInt8]()
    encoded.reserveCapacity(data.count * 2)
    for byte in data {
      encoded.append(hexadecimal[Int(byte >> 4)])
      encoded.append(hexadecimal[Int(byte & 0x0f)])
    }
    return String(decoding: encoded, as: UTF8.self)
  }
}

enum PushNotificationPayloadParser {
  enum ParseError: Error, Equatable {
    case invalidEventID
    case invalidCategory
    case invalidRoute
  }

  static func eventID(from userInfo: [AnyHashable: Any]) throws -> UUID {
    guard
      let rawEventID = userInfo["eventId"] as? String,
      let eventID = UUID(uuidString: rawEventID.trimmingCharacters(in: .whitespacesAndNewlines))
    else {
      throw ParseError.invalidEventID
    }
    guard
      let rawCategory = userInfo["category"] as? String,
      PushNotificationCategory(rawValue: rawCategory) != nil
    else {
      throw ParseError.invalidCategory
    }
    guard
      let rawRoute = userInfo["route"] as? String,
      NotificationRouteType(rawValue: rawRoute) != nil
    else {
      throw ParseError.invalidRoute
    }
    return eventID
  }
}

struct PushNotificationSessionIdentity: Equatable, Sendable {
  let contextID: UUID
  let userID: String
  let deviceSessionID: String
  let activeAccountID: String
  let authorizedAccountIDs: Set<String>
  let grantedScopes: Set<String>

  init(
    contextID: UUID = UUID(),
    userID: String,
    deviceSessionID: String,
    activeAccountID: String,
    authorizedAccountIDs: Set<String>,
    grantedScopes: Set<String>
  ) {
    self.contextID = contextID
    self.userID = userID
    self.deviceSessionID = deviceSessionID
    self.activeAccountID = activeAccountID
    self.authorizedAccountIDs = authorizedAccountIDs
    self.grantedScopes = grantedScopes
  }

  var isAuthorizedUniqueAccount: Bool {
    !userID.isEmpty
      && !deviceSessionID.isEmpty
      && authorizedAccountIDs == [activeAccountID]
      && grantedScopes.contains(NativeSessionScope.notificationManage.rawValue)
  }
}
