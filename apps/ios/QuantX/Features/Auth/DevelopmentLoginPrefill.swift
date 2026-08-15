import Foundation

struct DevelopmentLoginPrefill: Equatable {
  static let usernameEnvironmentKey = "QUANTX_IOS_DEVELOPMENT_USERNAME"
  static let passwordEnvironmentKey = "QUANTX_IOS_DEVELOPMENT_PASSWORD"

  let username: String
  let password: String

  var isConfigured: Bool {
    !username.isEmpty && !password.isEmpty
  }

  static func load(
    environment: [String: String] = ProcessInfo.processInfo.environment
  ) -> Self {
    #if DEBUG
      return Self(
        username: resolvedValue(environment[usernameEnvironmentKey])
          .trimmingCharacters(in: .whitespacesAndNewlines),
        password: resolvedValue(environment[passwordEnvironmentKey])
      )
    #else
      return Self(username: "", password: "")
    #endif
  }

  private static func resolvedValue(_ value: String?) -> String {
    guard let value, !value.contains("$(") else { return "" }
    return value
  }
}
