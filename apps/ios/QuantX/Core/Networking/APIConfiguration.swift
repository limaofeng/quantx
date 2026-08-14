import Foundation

struct APIConfiguration: Equatable, Sendable {
  enum Environment: String, Sendable {
    case debug
    case staging
    case production

    var displayName: String {
      switch self {
      case .debug: "开发"
      case .staging: "预发布"
      case .production: "生产"
      }
    }
  }

  enum ConfigurationError: LocalizedError, Equatable {
    case missingValue(String)
    case invalidValue(String, String)
    case insecureReleaseTransport(String)

    var errorDescription: String? {
      switch self {
      case .missingValue(let key):
        "缺少配置：\(key)"
      case .invalidValue(let key, let value):
        "配置 \(key) 无效：\(value)"
      case .insecureReleaseTransport(let key):
        "非 Debug 环境禁止为 \(key) 使用明文传输"
      }
    }
  }

  let environment: Environment
  let graphQLHTTPURL: URL
  let graphQLWebSocketURL: URL
  let healthURL: URL
  let authBaseURL: URL
  let accountDataEnabled: Bool

  var serviceHost: String {
    graphQLHTTPURL.host ?? "未知主机"
  }

  var usesInsecureAccountTransport: Bool {
    graphQLHTTPURL.scheme?.lowercased() == "http"
      || graphQLWebSocketURL.scheme?.lowercased() == "ws"
      || authBaseURL.scheme?.lowercased() == "http"
  }

  static func load(bundle: Bundle = .main) throws -> APIConfiguration {
    let environmentValue = try requiredString("QuantXEnvironment", bundle: bundle)
    guard let environment = Environment(rawValue: environmentValue) else {
      throw ConfigurationError.invalidValue("QuantXEnvironment", environmentValue)
    }

    let graphQLHTTPURL = try requiredURL("QuantXGraphQLHTTPURL", bundle: bundle)
    let graphQLWebSocketURL = try requiredURL("QuantXGraphQLWebSocketURL", bundle: bundle)
    let healthURL = try requiredURL("QuantXHealthURL", bundle: bundle)
    let authBaseURL = try requiredURL("QuantXAuthBaseURL", bundle: bundle)
    let accountDataEnabled = try requiredBool("QuantXAccountDataEnabled", bundle: bundle)

    if environment != .debug {
      guard graphQLHTTPURL.scheme == "https" else {
        throw ConfigurationError.insecureReleaseTransport("QuantXGraphQLHTTPURL")
      }
      guard graphQLWebSocketURL.scheme == "wss" else {
        throw ConfigurationError.insecureReleaseTransport("QuantXGraphQLWebSocketURL")
      }
      guard healthURL.scheme == "https" else {
        throw ConfigurationError.insecureReleaseTransport("QuantXHealthURL")
      }
      guard authBaseURL.scheme == "https" else {
        throw ConfigurationError.insecureReleaseTransport("QuantXAuthBaseURL")
      }
    }

    return APIConfiguration(
      environment: environment,
      graphQLHTTPURL: graphQLHTTPURL,
      graphQLWebSocketURL: graphQLWebSocketURL,
      healthURL: healthURL,
      authBaseURL: authBaseURL,
      accountDataEnabled: accountDataEnabled
    )
  }

  private static func requiredString(_ key: String, bundle: Bundle) throws -> String {
    guard let value = bundle.object(forInfoDictionaryKey: key) as? String,
      !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    else {
      throw ConfigurationError.missingValue(key)
    }
    return value
  }

  private static func requiredURL(_ key: String, bundle: Bundle) throws -> URL {
    let value = try requiredString(key, bundle: bundle)
    guard let url = URL(string: value), url.scheme != nil, url.host != nil else {
      throw ConfigurationError.invalidValue(key, value)
    }
    return url
  }

  private static func requiredBool(_ key: String, bundle: Bundle) throws -> Bool {
    let value = try requiredString(key, bundle: bundle).uppercased()
    switch value {
    case "YES", "TRUE", "1": return true
    case "NO", "FALSE", "0": return false
    default: throw ConfigurationError.invalidValue(key, value)
    }
  }
}
