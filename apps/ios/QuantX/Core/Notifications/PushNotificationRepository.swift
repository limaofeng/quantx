import Apollo
import Foundation

enum PushNotificationRepositoryError: LocalizedError, Equatable {
  case invalidResponse
  case contextMismatch
  case unauthenticated
  case forbidden
  case transport
  case rejected(code: String)

  var errorDescription: String? {
    switch self {
    case .invalidResponse:
      "通知服务返回了无法验证的数据"
    case .contextMismatch:
      "通知安装或账户会话已变化，请重新同步"
    case .unauthenticated:
      "登录会话已失效"
    case .forbidden:
      "当前会话没有 notification:manage 权限"
    case .transport:
      "无法连接通知服务，请检查私网或 VPN"
    case .rejected(let code):
      "通知服务拒绝了请求（\(code)）"
    }
  }
}

@MainActor
protocol PushNotificationLoading: AnyObject {
  func register(deviceToken: String, metadata: PushDeviceMetadata) async throws
    -> PushRegistrationSnapshot
  func updatePreferences(
    _ preferences: [PushNotificationCategory: Bool],
    metadata: PushDeviceMetadata
  ) async throws -> PushRegistrationSnapshot
  func unregister(metadata: PushDeviceMetadata) async throws
  func resolve(eventID: UUID) async throws -> NotificationRouteResolution?
}

@MainActor
final class PushNotificationRepository: PushNotificationLoading {
  private let client: ApolloClient
  private let noCache = RequestConfiguration(writeResultsToCache: false)

  init(client: ApolloClient) {
    self.client = client
  }

  func register(deviceToken: String, metadata: PushDeviceMetadata) async throws
    -> PushRegistrationSnapshot
  {
    guard
      !deviceToken.isEmpty,
      deviceToken.count.isMultiple(of: 2),
      deviceToken.allSatisfy({ $0.isHexDigit && !$0.isUppercase })
    else {
      throw PushNotificationRepositoryError.contextMismatch
    }
    do {
      let response = try await client.perform(
        mutation: QuantXAPI.IOSRegisterPushDeviceMutation(
          input: QuantXAPI.RegisterPushDeviceInput(
            deviceToken: deviceToken,
            environment: .init(Self.graphQLEnvironment(metadata.runtime.environment)),
            appBundleId: metadata.runtime.appBundleID,
            appVersion: metadata.runtime.appVersion,
            deviceInstallId: metadata.installationIDValue
          )
        ),
        requestConfiguration: noCache
      )
      try validate(response.errors)
      let value = response.data?.registerPushDevice
      guard let value else {
        throw PushNotificationRepositoryError.invalidResponse
      }
      return try Self.mapRegistration(
        id: value.id,
        deviceInstallID: value.deviceInstallId,
        appBundleID: value.appBundleId,
        appVersion: value.appVersion,
        environment: value.environment.value,
        registeredAt: value.registeredAt,
        updatedAt: value.updatedAt,
        preferences: value.preferences.map { ($0.category.value, $0.enabled) },
        expectedMetadata: metadata
      )
    } catch {
      throw map(error)
    }
  }

  func updatePreferences(
    _ preferences: [PushNotificationCategory: Bool],
    metadata: PushDeviceMetadata
  ) async throws -> PushRegistrationSnapshot {
    guard Set(preferences.keys) == Set(PushNotificationCategory.allCases) else {
      throw PushNotificationRepositoryError.contextMismatch
    }
    let inputs = PushNotificationCategory.allCases.map { category in
      QuantXAPI.PushCategoryPreferenceInput(
        category: .init(Self.graphQLCategory(category)),
        enabled: preferences[category] == true
      )
    }
    do {
      let response = try await client.perform(
        mutation: QuantXAPI.IOSUpdatePushPreferencesMutation(
          input: QuantXAPI.UpdatePushPreferencesInput(
            environment: .init(Self.graphQLEnvironment(metadata.runtime.environment)),
            appBundleId: metadata.runtime.appBundleID,
            deviceInstallId: metadata.installationIDValue,
            preferences: inputs
          )
        ),
        requestConfiguration: noCache
      )
      try validate(response.errors)
      let value = response.data?.updatePushPreferences
      guard let value else {
        throw PushNotificationRepositoryError.invalidResponse
      }
      return try Self.mapRegistration(
        id: value.id,
        deviceInstallID: value.deviceInstallId,
        appBundleID: value.appBundleId,
        appVersion: value.appVersion,
        environment: value.environment.value,
        registeredAt: value.registeredAt,
        updatedAt: value.updatedAt,
        preferences: value.preferences.map { ($0.category.value, $0.enabled) },
        expectedMetadata: metadata
      )
    } catch {
      throw map(error)
    }
  }

  func unregister(metadata: PushDeviceMetadata) async throws {
    do {
      let response = try await client.perform(
        mutation: QuantXAPI.IOSUnregisterPushDeviceMutation(
          input: QuantXAPI.UnregisterPushDeviceInput(
            environment: .init(Self.graphQLEnvironment(metadata.runtime.environment)),
            appBundleId: metadata.runtime.appBundleID,
            deviceInstallId: metadata.installationIDValue
          )
        ),
        requestConfiguration: noCache
      )
      try validate(response.errors)
      guard response.data?.unregisterPushDevice.success == true else {
        throw PushNotificationRepositoryError.invalidResponse
      }
    } catch {
      throw map(error)
    }
  }

  func resolve(eventID: UUID) async throws -> NotificationRouteResolution? {
    do {
      let response = try await client.fetch(
        query: QuantXAPI.IOSNotificationEventRouteQuery(
          eventId: eventID.uuidString.lowercased()
        ),
        cachePolicy: .networkOnly
      )
      try validate(response.errors)
      guard let value = response.data?.notificationEventRoute else {
        return nil
      }
      return try Self.mapRoute(
        eventID: value.eventId,
        category: value.category.value,
        route: value.routeType.value,
        occurredAt: value.occurredAt,
        expiresAt: value.expiresAt,
        expired: value.expired,
        expectedEventID: eventID
      )
    } catch {
      throw map(error)
    }
  }

  static func mapRegistration(
    id: String,
    deviceInstallID: String,
    appBundleID: String,
    appVersion: String,
    environment: QuantXAPI.PushEnvironment?,
    registeredAt: String,
    updatedAt: String,
    preferences: [(QuantXAPI.PushCategory?, Bool)],
    expectedMetadata: PushDeviceMetadata
  ) throws -> PushRegistrationSnapshot {
    guard
      let registrationID = UUID(uuidString: id),
      let installationID = UUID(uuidString: deviceInstallID),
      installationID == expectedMetadata.installationID,
      appBundleID == expectedMetadata.runtime.appBundleID,
      appVersion == expectedMetadata.runtime.appVersion,
      environment == graphQLEnvironment(expectedMetadata.runtime.environment)
    else {
      throw PushNotificationRepositoryError.contextMismatch
    }
    var mapped: [PushNotificationCategory: Bool] = [:]
    for (rawCategory, enabled) in preferences {
      guard
        let rawCategory,
        let category = notificationCategory(rawCategory),
        mapped[category] == nil
      else {
        throw PushNotificationRepositoryError.invalidResponse
      }
      mapped[category] = enabled
    }
    guard Set(mapped.keys) == Set(PushNotificationCategory.allCases) else {
      throw PushNotificationRepositoryError.invalidResponse
    }
    return PushRegistrationSnapshot(
      id: registrationID.uuidString.lowercased(),
      metadata: expectedMetadata,
      registeredAt: try ReadOnlyModelValidator.requireDate(
        registeredAt,
        field: "push.registeredAt"
      ),
      updatedAt: try ReadOnlyModelValidator.requireDate(
        updatedAt,
        field: "push.updatedAt"
      ),
      preferences: mapped
    )
  }

  static func mapRoute(
    eventID: String,
    category: QuantXAPI.PushCategory?,
    route: QuantXAPI.NotificationRouteType?,
    occurredAt: String,
    expiresAt: String,
    expired: Bool,
    expectedEventID: UUID
  ) throws -> NotificationRouteResolution {
    guard
      let responseEventID = UUID(uuidString: eventID),
      responseEventID == expectedEventID,
      let category,
      let mappedCategory = notificationCategory(category),
      let route,
      let mappedRoute = notificationRoute(route)
    else {
      throw PushNotificationRepositoryError.contextMismatch
    }
    let occurred = try ReadOnlyModelValidator.requireDate(
      occurredAt,
      field: "push.route.occurredAt"
    )
    let expires = try ReadOnlyModelValidator.requireDate(
      expiresAt,
      field: "push.route.expiresAt"
    )
    guard occurred <= expires else {
      throw PushNotificationRepositoryError.invalidResponse
    }
    return NotificationRouteResolution(
      eventID: expectedEventID,
      category: mappedCategory,
      route: mappedRoute,
      occurredAt: occurred,
      expiresAt: expires,
      expired: expired
    )
  }

  private func validate(_ errors: [GraphQLError]?) throws {
    do {
      try ApolloReadOnlyResponseValidator.validate(errors)
    } catch {
      throw map(error)
    }
  }

  private func map(_ error: Error) -> Error {
    if error is CancellationError {
      return CancellationError()
    }
    if let error = error as? PushNotificationRepositoryError {
      return error
    }
    if let error = error as? ReadOnlyRepositoryError {
      switch error {
      case .unauthenticated: return PushNotificationRepositoryError.unauthenticated
      case .forbidden: return PushNotificationRepositoryError.forbidden
      case .accountScopeMismatch: return PushNotificationRepositoryError.contextMismatch
      case .invalidResponse: return PushNotificationRepositoryError.invalidResponse
      case .transport: return PushNotificationRepositoryError.transport
      case .graphQL(let code, _): return PushNotificationRepositoryError.rejected(code: code)
      }
    }
    if error is ReadOnlyMappingError {
      return PushNotificationRepositoryError.invalidResponse
    }
    if let error = error as? ResponseCodeInterceptor.ResponseCodeError {
      switch error.response.statusCode {
      case 401: return PushNotificationRepositoryError.unauthenticated
      case 403: return PushNotificationRepositoryError.forbidden
      default: return PushNotificationRepositoryError.transport
      }
    }
    return PushNotificationRepositoryError.transport
  }

  private static func graphQLEnvironment(
    _ environment: PushNotificationEnvironment
  ) -> QuantXAPI.PushEnvironment {
    switch environment {
    case .sandbox: .sandbox
    case .production: .production
    }
  }

  private static func graphQLCategory(
    _ category: PushNotificationCategory
  ) -> QuantXAPI.PushCategory {
    switch category {
    case .actionRequired: .actionRequired
    case .orderUpdate: .orderUpdate
    case .riskSafety: .riskSafety
    case .automationError: .automationError
    case .connectionData: .connectionData
    }
  }

  private static func notificationCategory(
    _ category: QuantXAPI.PushCategory
  ) -> PushNotificationCategory? {
    PushNotificationCategory(rawValue: category.rawValue)
  }

  private static func notificationRoute(
    _ route: QuantXAPI.NotificationRouteType
  ) -> NotificationRouteType? {
    switch route {
    case .todayAction: .todayAction
    case .tradingOrders: .tradingOrders
    case .tradingSafety: .tradingSafety
    case .quantWorkspace: .quantWorkspace
    case .systemStatus: .systemStatus
    }
  }
}
