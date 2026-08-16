import Foundation

struct SessionUser: Codable, Equatable, Identifiable, Sendable {
  let id: String
  let username: String
  let displayName: String
  let permissions: [String]
  let authorizedAccountIDs: [String]

  var activeAccountID: String? {
    authorizedAccountIDs.count == 1 ? authorizedAccountIDs[0] : nil
  }

  private enum CodingKeys: String, CodingKey {
    case id
    case username
    case displayName
    case permissions
    case authorizedAccountIDs = "authorizedAccountIds"
  }
}

struct AuthenticatedSession: Equatable, Sendable {
  let tokens: SessionTokens
  let user: SessionUser
}

protocol SessionServing: Sendable {
  func login(
    username: String,
    password: String,
    deviceName: String
  ) async throws -> AuthenticatedSession
  func refresh(refreshToken: String) async throws -> AuthenticatedSession
  func current(accessToken: String) async throws -> SessionUser
  func logout(accessToken: String, allDevices: Bool) async throws
}

actor SessionClient: SessionServing {
  enum ClientError: LocalizedError, Equatable, Sendable {
    case secureTransportRequired
    case invalidResponse
    case server(code: String, message: String, requestID: String?, retryable: Bool)

    var errorDescription: String? {
      switch self {
      case .secureTransportRequired:
        "登录凭证和账户会话只允许通过 HTTPS 传输"
      case .invalidResponse:
        "认证服务返回了无法识别的响应"
      case .server(_, let message, _, _):
        message
      }
    }
  }

  private struct LoginBody: Encodable {
    let username: String
    let password: String
    let deviceName: String
  }

  private struct RefreshBody: Encodable {
    let refreshToken: String
  }

  private struct GrantResponse: Decodable {
    let accessToken: String
    let refreshToken: String
    let accessTokenExpiresAt: Date
    let refreshTokenExpiresAt: Date
    let deviceSessionID: String
    let user: SessionUser

    private enum CodingKeys: String, CodingKey {
      case accessToken
      case refreshToken
      case accessTokenExpiresAt
      case refreshTokenExpiresAt
      case deviceSessionID = "deviceSessionId"
      case user
    }
  }

  private struct StateResponse: Decodable {
    let deviceSessionID: String
    let accessTokenExpiresAt: Date
    let user: SessionUser

    private enum CodingKeys: String, CodingKey {
      case deviceSessionID = "deviceSessionId"
      case accessTokenExpiresAt
      case user
    }
  }

  private struct ErrorEnvelope: Decodable {
    struct Detail: Decodable {
      let code: String
      let message: String
      let requestID: String?
      let retryable: Bool

      private enum CodingKeys: String, CodingKey {
        case code
        case message
        case requestID = "requestId"
        case retryable
      }
    }

    let detail: Detail
  }

  private let baseURL: URL
  private let session: URLSession
  private let encoder = JSONEncoder()
  private let decoder: JSONDecoder
  private var refreshTask: Task<AuthenticatedSession, Error>?
  private var currentAuthorization: SessionUser?

  init(
    baseURL: URL,
    session: URLSession? = nil,
    allowsInsecureDevelopmentTransport: Bool = false
  ) throws {
    let scheme = baseURL.scheme?.lowercased()
    guard scheme == "https" || (allowsInsecureDevelopmentTransport && scheme == "http") else {
      throw ClientError.secureTransportRequired
    }
    self.baseURL = baseURL
    if let session {
      self.session = session
    } else {
      let configuration = URLSessionConfiguration.ephemeral
      configuration.timeoutIntervalForRequest = 15
      configuration.timeoutIntervalForResource = 30
      configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
      self.session = URLSession(configuration: configuration)
    }
    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .iso8601
    self.decoder = decoder
  }

  func login(
    username: String,
    password: String,
    deviceName: String
  ) async throws -> AuthenticatedSession {
    let body = try encoder.encode(
      LoginBody(
        username: username,
        password: password,
        deviceName: deviceName
      )
    )
    let response: GrantResponse = try await request(
      path: "auth/session",
      method: "POST",
      body: body
    )
    let authenticated = try authenticatedSession(
      from: response,
      previousAuthorization: nil
    )
    currentAuthorization = authenticated.user
    return authenticated
  }

  func refresh(refreshToken: String) async throws -> AuthenticatedSession {
    if let refreshTask {
      return try await refreshTask.value
    }
    let task = Task { [self] in
      try await performRefresh(refreshToken: refreshToken)
    }
    refreshTask = task
    defer { refreshTask = nil }
    return try await task.value
  }

  private func performRefresh(refreshToken: String) async throws -> AuthenticatedSession {
    let body = try encoder.encode(RefreshBody(refreshToken: refreshToken))
    let response: GrantResponse = try await request(
      path: "auth/session/refresh",
      method: "POST",
      body: body
    )
    let authenticated = try authenticatedSession(
      from: response,
      previousAuthorization: currentAuthorization
    )
    currentAuthorization = authenticated.user
    return authenticated
  }

  func current(accessToken: String) async throws -> SessionUser {
    let response: StateResponse = try await request(
      path: "auth/session",
      method: "GET",
      accessToken: accessToken
    )
    let user = try validatedUser(
      response.user,
      previousAuthorization: currentAuthorization
    )
    currentAuthorization = user
    return user
  }

  func logout(accessToken: String, allDevices: Bool) async throws {
    defer { currentAuthorization = nil }
    var components = URLComponents(
      url: endpoint(path: "auth/session"), resolvingAgainstBaseURL: false
    )
    if allDevices {
      components?.queryItems = [URLQueryItem(name: "allDevices", value: "true")]
    }
    guard let url = components?.url else { throw ClientError.invalidResponse }
    var request = URLRequest(url: url)
    request.httpMethod = "DELETE"
    request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
    request.setValue(UUID().uuidString, forHTTPHeaderField: "X-Request-ID")
    let (data, response) = try await session.data(for: request)
    try validate(response: response, data: data, expectedStatus: 204)
  }

  private func authenticatedSession(
    from response: GrantResponse,
    previousAuthorization: SessionUser?
  ) throws -> AuthenticatedSession {
    let user = try validatedUser(
      response.user,
      previousAuthorization: previousAuthorization
    )
    return AuthenticatedSession(
      tokens: SessionTokens(
        accessToken: response.accessToken,
        refreshToken: response.refreshToken,
        accessTokenExpiresAt: response.accessTokenExpiresAt,
        refreshTokenExpiresAt: response.refreshTokenExpiresAt,
        deviceSessionID: response.deviceSessionID
      ),
      user: user
    )
  }

  private func validatedUser(
    _ user: SessionUser,
    previousAuthorization: SessionUser?
  ) throws -> SessionUser {
    guard let activeAccountID = user.activeAccountID else {
      throw ClientError.invalidResponse
    }
    let normalizedAccountID = activeAccountID.trimmingCharacters(
      in: .whitespacesAndNewlines
    )
    let grantedScopes = user.permissions
    let grantedSet = Set(grantedScopes)
    guard
      !normalizedAccountID.isEmpty,
      normalizedAccountID == activeAccountID,
      user.authorizedAccountIDs.count == 1,
      user.authorizedAccountIDs[0] == normalizedAccountID,
      grantedSet.count == grantedScopes.count,
      grantedScopes.allSatisfy({ scope in
        !scope.isEmpty
          && scope == scope.trimmingCharacters(in: .whitespacesAndNewlines)
          && NativeSessionScope.v1AllowedValues.contains(scope)
      })
    else {
      throw ClientError.invalidResponse
    }
    if let previousAuthorization {
      guard
        previousAuthorization.activeAccountID == normalizedAccountID,
        grantedSet.isSubset(of: Set(previousAuthorization.permissions))
      else {
        throw ClientError.invalidResponse
      }
    }
    return user
  }

  private func request<Response: Decodable>(
    path: String,
    method: String,
    body: Data? = nil,
    accessToken: String? = nil
  ) async throws -> Response {
    var request = URLRequest(url: endpoint(path: path))
    request.httpMethod = method
    request.httpBody = body
    request.setValue("application/json", forHTTPHeaderField: "Accept")
    request.setValue(UUID().uuidString, forHTTPHeaderField: "X-Request-ID")
    if body != nil {
      request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    }
    if let accessToken {
      request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
    }
    let (data, response) = try await session.data(for: request)
    try validate(response: response, data: data, expectedStatus: 200)
    do {
      return try decoder.decode(Response.self, from: data)
    } catch {
      throw ClientError.invalidResponse
    }
  }

  private func validate(
    response: URLResponse,
    data: Data,
    expectedStatus: Int
  ) throws {
    guard let response = response as? HTTPURLResponse else {
      throw ClientError.invalidResponse
    }
    guard response.statusCode == expectedStatus else {
      if let envelope = try? decoder.decode(ErrorEnvelope.self, from: data) {
        throw ClientError.server(
          code: envelope.detail.code,
          message: envelope.detail.message,
          requestID: envelope.detail.requestID,
          retryable: envelope.detail.retryable
        )
      }
      throw ClientError.server(
        code: "HTTP_\(response.statusCode)",
        message: "认证服务暂时不可用",
        requestID: response.value(forHTTPHeaderField: "X-Request-ID"),
        retryable: response.statusCode >= 500
      )
    }
  }

  private func endpoint(path: String) -> URL {
    baseURL.appending(path: path)
  }
}
