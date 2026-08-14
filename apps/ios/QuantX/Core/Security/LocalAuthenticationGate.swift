import LocalAuthentication

@MainActor
protocol LocalAuthenticationProviding {
  func unlock(reason: String) async throws
  func authorizeTrade(reason: String) async throws
}

extension LocalAuthenticationProviding {
  func authorizeTrade(reason: String) async throws {
    try await unlock(reason: reason)
  }
}

@MainActor
final class LocalAuthenticationGate: LocalAuthenticationProviding {
  enum GateError: LocalizedError {
    case unavailable
    case biometricsUnavailable

    var errorDescription: String? {
      switch self {
      case .unavailable:
        "此设备尚未启用 Face ID、Touch ID 或设备密码"
      case .biometricsUnavailable:
        "交易确认要求启用 Face ID 或 Touch ID"
      }
    }
  }

  func unlock(reason: String) async throws {
    let context = LAContext()
    context.localizedCancelTitle = "取消"
    context.localizedFallbackTitle = "使用设备密码"

    var evaluationError: NSError?
    let policy: LAPolicy
    if context.canEvaluatePolicy(
      .deviceOwnerAuthenticationWithBiometrics,
      error: &evaluationError
    ) {
      policy = .deviceOwnerAuthenticationWithBiometrics
    } else if context.canEvaluatePolicy(
      .deviceOwnerAuthentication,
      error: &evaluationError
    ) {
      policy = .deviceOwnerAuthentication
    } else {
      throw GateError.unavailable
    }

    try await context.evaluatePolicy(policy, localizedReason: reason)
  }

  func authorizeTrade(reason: String) async throws {
    let context = LAContext()
    context.localizedCancelTitle = "取消确认"
    context.localizedFallbackTitle = ""

    var evaluationError: NSError?
    guard context.canEvaluatePolicy(
      .deviceOwnerAuthenticationWithBiometrics,
      error: &evaluationError
    ) else {
      throw GateError.biometricsUnavailable
    }
    try await context.evaluatePolicy(
      .deviceOwnerAuthenticationWithBiometrics,
      localizedReason: reason
    )
  }
}
