import Foundation

protocol HealthChecking: Sendable {
  func fetch() async throws -> HealthSnapshot
}

actor HealthClient: HealthChecking {
  enum HealthError: LocalizedError {
    case invalidResponse
    case httpStatus(Int)

    var errorDescription: String? {
      switch self {
      case .invalidResponse: "服务返回了无法识别的响应"
      case .httpStatus(let code): "服务健康检查失败（HTTP \(code)）"
      }
    }
  }

  private let endpoint: URL
  private let session: URLSession
  private let decoder: JSONDecoder

  init(endpoint: URL, session: URLSession = .shared) {
    self.endpoint = endpoint
    self.session = session
    decoder = JSONDecoder()
  }

  func fetch() async throws -> HealthSnapshot {
    var request = URLRequest(url: endpoint)
    request.httpMethod = "GET"
    request.timeoutInterval = 10
    request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData

    let (data, response) = try await session.data(for: request)
    guard let httpResponse = response as? HTTPURLResponse else {
      throw HealthError.invalidResponse
    }
    guard (200...299).contains(httpResponse.statusCode) else {
      throw HealthError.httpStatus(httpResponse.statusCode)
    }
    do {
      return try decoder.decode(HealthSnapshot.self, from: data)
    } catch {
      throw HealthError.invalidResponse
    }
  }
}
