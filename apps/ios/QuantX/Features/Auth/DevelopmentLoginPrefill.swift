import Foundation

struct DevelopmentLoginPrefill: Equatable {
  static let usernameEnvironmentKey = "QUANTX_IOS_DEVELOPMENT_USERNAME"
  static let passwordEnvironmentKey = "QUANTX_IOS_DEVELOPMENT_PASSWORD"
  static let usernameBundleKey = "QuantXDevelopmentUsername"
  static let passwordBundleKey = "QuantXDevelopmentPassword"

  let username: String
  let password: String

  var isConfigured: Bool {
    !username.isEmpty && !password.isEmpty
  }

  static func load(
    environment: [String: String] = ProcessInfo.processInfo.environment,
    bundleInfo: [String: Any] = Bundle.main.infoDictionary ?? [:]
  ) -> Self {
    #if DEBUG
      return Self(
        username: firstResolvedValue(
          environment[usernameEnvironmentKey],
          bundleInfo[usernameBundleKey] as? String
        )
        .trimmingCharacters(in: .whitespacesAndNewlines),
        password: firstResolvedValue(
          environment[passwordEnvironmentKey],
          bundleInfo[passwordBundleKey] as? String
        )
      )
    #else
      return Self(username: "", password: "")
    #endif
  }

  private static func firstResolvedValue(_ values: String?...) -> String {
    for value in values {
      let resolved = resolvedValue(value)
      if !resolved.isEmpty { return resolved }
    }
    return ""
  }

  private static func resolvedValue(_ value: String?) -> String {
    guard let value, !value.contains("$(") else { return "" }
    return value
  }
}
