import Foundation
import XCTest

@testable import QuantX

final class SessionClientTests: XCTestCase {
  override func setUp() {
    super.setUp()
    URLProtocolStub.reset()
  }

  override func tearDown() {
    URLProtocolStub.reset()
    super.tearDown()
  }

  func testLoginUsesDeployedRequestFieldNames() async throws {
    URLProtocolStub.install { request in
      XCTAssertEqual(request.url?.path, "/auth/session")
      XCTAssertEqual(request.httpMethod, "POST")
      XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
      XCTAssertNotNil(request.value(forHTTPHeaderField: "X-Request-ID"))

      let body = try XCTUnwrap(URLProtocolStub.bodyData(for: request))
      let json = try XCTUnwrap(
        JSONSerialization.jsonObject(with: body) as? [String: Any]
      )
      XCTAssertEqual(json["username"] as? String, "ios-user")
      XCTAssertEqual(json["password"] as? String, "safe-test-password")
      XCTAssertEqual(json["deviceName"] as? String, "iPhone 17 Pro")
      XCTAssertEqual(Set(json.keys), ["username", "password", "deviceName"])
      return URLProtocolStub.Response(statusCode: 200, body: Self.grantPayload)
    }
    let client = try makeClient()

    let session = try await client.login(
      username: "ios-user",
      password: "safe-test-password",
      deviceName: "iPhone 17 Pro"
    )

    XCTAssertEqual(session.user.username, "ios-user")
    XCTAssertEqual(session.tokens.deviceSessionID, "device-session-id")
    XCTAssertEqual(session.user.activeAccountID, "account-id")
    XCTAssertEqual(session.user.permissions, ["portfolio:read"])
  }

  func testLogoutUsesBearerAndAllDevicesQuery() async throws {
    URLProtocolStub.install { request in
      XCTAssertEqual(request.url?.path, "/auth/session")
      XCTAssertEqual(request.httpMethod, "DELETE")
      XCTAssertEqual(
        request.value(forHTTPHeaderField: "Authorization"),
        "Bearer access-token"
      )
      XCTAssertNotNil(request.value(forHTTPHeaderField: "X-Request-ID"))
      let components = try XCTUnwrap(
        URLComponents(url: XCTUnwrap(request.url), resolvingAgainstBaseURL: false)
      )
      XCTAssertEqual(
        components.queryItems,
        [URLQueryItem(name: "allDevices", value: "true")]
      )
      return URLProtocolStub.Response(statusCode: 204, body: Data())
    }
    let client = try makeClient()

    try await client.logout(accessToken: "access-token", allDevices: true)
  }

  func testConcurrentRefreshCallsShareOneRotationRequest() async throws {
    URLProtocolStub.install { request in
      XCTAssertEqual(request.url?.path, "/auth/session/refresh")
      XCTAssertEqual(request.httpMethod, "POST")
      XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
      XCTAssertNotNil(request.value(forHTTPHeaderField: "X-Request-ID"))

      let body = try XCTUnwrap(URLProtocolStub.bodyData(for: request))
      let json = try XCTUnwrap(
        JSONSerialization.jsonObject(with: body) as? [String: String]
      )
      XCTAssertEqual(json, ["refreshToken": "old-refresh-token"])

      return URLProtocolStub.Response(
        statusCode: 200,
        body: Self.grantPayload,
        delay: 0.1
      )
    }
    let client = try makeClient()

    async let first = client.refresh(refreshToken: "old-refresh-token")
    async let second = client.refresh(refreshToken: "old-refresh-token")
    let (firstSession, secondSession) = try await (first, second)

    XCTAssertEqual(firstSession, secondSession)
    XCTAssertEqual(firstSession.tokens.accessToken, "new-access-token")
    XCTAssertEqual(firstSession.tokens.refreshToken, "new-refresh-token")
    XCTAssertEqual(firstSession.tokens.deviceSessionID, "device-session-id")
    XCTAssertEqual(firstSession.user.authorizedAccountIDs, ["account-id"])
    XCTAssertEqual(URLProtocolStub.requestCount, 1)
  }

  func testCurrentSessionDecodesServerIDFieldCasing() async throws {
    URLProtocolStub.install { request in
      XCTAssertEqual(request.url?.path, "/auth/session")
      XCTAssertEqual(request.httpMethod, "GET")
      XCTAssertEqual(
        request.value(forHTTPHeaderField: "Authorization"),
        "Bearer access-token"
      )
      return URLProtocolStub.Response(statusCode: 200, body: Self.statePayload)
    }
    let client = try makeClient()

    let user = try await client.current(accessToken: "access-token")

    XCTAssertEqual(user.id, "user-id")
    XCTAssertEqual(user.authorizedAccountIDs, ["account-id"])
    XCTAssertEqual(user.activeAccountID, "account-id")
    XCTAssertEqual(user.permissions, ["portfolio:read"])
  }

  func testUserPermissionsRejectCapabilitiesOutsideNativeAllowlist() async throws {
    URLProtocolStub.install { _ in
      URLProtocolStub.Response(
        statusCode: 200,
        body: Self.makeGrantPayload(
          permissions: ["portfolio:read", "mutation:write", "trade:direct"]
        )
      )
    }
    let client = try makeClient()

    do {
      _ = try await client.login(
        username: "ios-user",
        password: "safe-test-password",
        deviceName: "iPhone"
      )
      XCTFail("Expected invalidResponse")
    } catch let error as SessionClient.ClientError {
      XCTAssertEqual(error, .invalidResponse)
    }
  }

  func testSessionRejectsNonUniqueOrSubstitutedAccountContext() async throws {
    URLProtocolStub.install { _ in
      URLProtocolStub.Response(
        statusCode: 200,
        body: Self.makeGrantPayload(
          authorizedAccountIDs: ["account-id", "other-account"]
        )
      )
    }
    let client = try makeClient()

    do {
      _ = try await client.login(
        username: "ios-user",
        password: "safe-test-password",
        deviceName: "iPhone"
      )
      XCTFail("Expected invalidResponse")
    } catch let error as SessionClient.ClientError {
      XCTAssertEqual(error, .invalidResponse)
    }
  }

  func testRefreshMayShrinkButNeverExpandPermissions() async throws {
    URLProtocolStub.install { request in
      switch request.url?.path {
      case "/auth/session":
        return URLProtocolStub.Response(
          statusCode: 200,
          body: Self.makeGrantPayload(
            permissions: ["portfolio:read", "market:read"]
          )
        )
      case "/auth/session/refresh":
        return URLProtocolStub.Response(
          statusCode: 200,
          body: Self.makeGrantPayload(permissions: ["portfolio:read"])
        )
      default:
        throw URLError(.badURL)
      }
    }
    let client = try makeClient()
    _ = try await client.login(
      username: "ios-user",
      password: "safe-test-password",
      deviceName: "iPhone"
    )

    let refreshed = try await client.refresh(refreshToken: "old-refresh-token")

    XCTAssertEqual(refreshed.user.permissions, ["portfolio:read"])

    URLProtocolStub.install { request in
      switch request.url?.path {
      case "/auth/session":
        return URLProtocolStub.Response(statusCode: 200, body: Self.grantPayload)
      case "/auth/session/refresh":
        return URLProtocolStub.Response(
          statusCode: 200,
          body: Self.makeGrantPayload(
            permissions: ["portfolio:read", "market:read"]
          )
        )
      default:
        throw URLError(.badURL)
      }
    }
    let expansionClient = try makeClient()
    _ = try await expansionClient.login(
      username: "ios-user",
      password: "safe-test-password",
      deviceName: "iPhone"
    )

    do {
      _ = try await expansionClient.refresh(refreshToken: "old-refresh-token")
      XCTFail("Expected scope expansion to be rejected")
    } catch let error as SessionClient.ClientError {
      XCTAssertEqual(error, .invalidResponse)
    }
  }

  func testStructuredAuthenticationErrorKeepsSafeCodeAndRequestID() async throws {
    URLProtocolStub.install { request in
      XCTAssertEqual(
        request.value(forHTTPHeaderField: "Authorization"),
        "Bearer expired-access-token"
      )
      return URLProtocolStub.Response(
        statusCode: 401,
        body: Data(
          #"{"detail":{"code":"UNAUTHENTICATED","message":"会话已失效","requestId":"request-safe-id","retryable":false}}"#
            .utf8
        )
      )
    }
    let client = try makeClient()

    do {
      _ = try await client.current(accessToken: "expired-access-token")
      XCTFail("Expected an authentication error")
    } catch let SessionClient.ClientError.server(code, message, requestID, retryable) {
      XCTAssertEqual(code, "UNAUTHENTICATED")
      XCTAssertEqual(message, "会话已失效")
      XCTAssertEqual(requestID, "request-safe-id")
      XCTAssertFalse(retryable)
    } catch {
      XCTFail("Unexpected error: \(error)")
    }
  }

  func testUnstructuredServerFailureUsesGenericMessage() async throws {
    URLProtocolStub.install { _ in
      URLProtocolStub.Response(
        statusCode: 502,
        headers: ["X-Request-ID": "gateway-request-id"],
        body: Data("upstream stack trace must not reach UI".utf8)
      )
    }
    let client = try makeClient()

    do {
      _ = try await client.current(accessToken: "access-token")
      XCTFail("Expected a server error")
    } catch let SessionClient.ClientError.server(code, message, requestID, retryable) {
      XCTAssertEqual(code, "HTTP_502")
      XCTAssertEqual(message, "认证服务暂时不可用")
      XCTAssertEqual(requestID, "gateway-request-id")
      XCTAssertTrue(retryable)
      XCTAssertFalse(message.contains("stack trace"))
    } catch {
      XCTFail("Unexpected error: \(error)")
    }
  }

  func testInvalidSuccessfulPayloadIsRejected() async throws {
    URLProtocolStub.install { _ in
      URLProtocolStub.Response(statusCode: 200, body: Data(#"{"unexpected":true}"#.utf8))
    }
    let client = try makeClient()

    do {
      _ = try await client.refresh(refreshToken: "refresh-token")
      XCTFail("Expected invalidResponse")
    } catch let error as SessionClient.ClientError {
      XCTAssertEqual(error, .invalidResponse)
    }
  }

  private func makeClient() throws -> SessionClient {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [URLProtocolStub.self]
    let session = URLSession(configuration: configuration)
    return try SessionClient(
      baseURL: XCTUnwrap(URL(string: "https://quantx.test")),
      session: session
    )
  }

  private static let grantPayload = makeGrantPayload()

  private static let statePayload = makeStatePayload()

  private static func makeGrantPayload(
    authorizedAccountIDs: [String] = ["account-id"],
    permissions: [String] = ["portfolio:read"]
  ) -> Data {
    payload(
      base: [
        "accessToken": "new-access-token",
        "refreshToken": "new-refresh-token",
        "accessTokenExpiresAt": "2026-07-21T12:00:00Z",
        "refreshTokenExpiresAt": "2026-08-20T12:00:00Z",
        "deviceSessionId": "device-session-id",
        "tokenType": "Bearer",
      ],
      authorizedAccountIDs: authorizedAccountIDs,
      permissions: permissions
    )
  }

  private static func makeStatePayload(
    authorizedAccountIDs: [String] = ["account-id"],
    permissions: [String] = ["portfolio:read"]
  ) -> Data {
    payload(
      base: [
        "accessTokenExpiresAt": "2026-07-21T12:00:00Z",
        "deviceSessionId": "device-session-id",
      ],
      authorizedAccountIDs: authorizedAccountIDs,
      permissions: permissions
    )
  }

  private static func payload(
    base: [String: Any],
    authorizedAccountIDs: [String],
    permissions: [String]
  ) -> Data {
    var value = base
    value["user"] = [
      "id": "user-id",
      "username": "ios-user",
      "displayName": "iOS 用户",
      "permissions": permissions,
      "authorizedAccountIds": authorizedAccountIDs,
    ]
    return try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
  }
}

private final class URLProtocolStub: URLProtocol, @unchecked Sendable {
  struct Response: Sendable {
    let statusCode: Int
    let headers: [String: String]
    let body: Data
    let delay: TimeInterval

    init(
      statusCode: Int,
      headers: [String: String] = ["Content-Type": "application/json"],
      body: Data,
      delay: TimeInterval = 0
    ) {
      self.statusCode = statusCode
      self.headers = headers
      self.body = body
      self.delay = delay
    }
  }

  typealias Handler = @Sendable (URLRequest) throws -> Response

  private static let lock = NSLock()
  nonisolated(unsafe) private static var handler: Handler?
  nonisolated(unsafe) private static var requests: [URLRequest] = []

  private var workItem: DispatchWorkItem?

  static var requestCount: Int {
    lock.lock()
    defer { lock.unlock() }
    return requests.count
  }

  static func bodyData(for request: URLRequest) -> Data? {
    if let body = request.httpBody {
      return body
    }
    guard let stream = request.httpBodyStream else { return nil }
    stream.open()
    defer { stream.close() }

    var data = Data()
    var buffer = [UInt8](repeating: 0, count: 1_024)
    while true {
      let count = buffer.withUnsafeMutableBufferPointer { pointer in
        guard let baseAddress = pointer.baseAddress else { return -1 }
        return stream.read(baseAddress, maxLength: pointer.count)
      }
      if count < 0 { return nil }
      if count == 0 { return data }
      data.append(buffer, count: count)
    }
  }

  static func install(_ handler: @escaping Handler) {
    lock.lock()
    Self.handler = handler
    requests = []
    lock.unlock()
  }

  static func reset() {
    lock.lock()
    handler = nil
    requests = []
    lock.unlock()
  }

  override class func canInit(with _: URLRequest) -> Bool {
    true
  }

  override class func canonicalRequest(for request: URLRequest) -> URLRequest {
    request
  }

  override func startLoading() {
    Self.lock.lock()
    let handler = Self.handler
    Self.requests.append(request)
    Self.lock.unlock()

    guard let handler else {
      client?.urlProtocol(self, didFailWithError: URLError(.resourceUnavailable))
      return
    }

    do {
      let stubbedResponse = try handler(request)
      let workItem = DispatchWorkItem { [weak self] in
        guard let self else { return }
        guard
          let url = self.request.url,
          let response = HTTPURLResponse(
            url: url,
            statusCode: stubbedResponse.statusCode,
            httpVersion: "HTTP/1.1",
            headerFields: stubbedResponse.headers
          )
        else {
          self.client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
          return
        }
        self.client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        self.client?.urlProtocol(self, didLoad: stubbedResponse.body)
        self.client?.urlProtocolDidFinishLoading(self)
      }
      self.workItem = workItem
      DispatchQueue.global().asyncAfter(
        deadline: .now() + stubbedResponse.delay,
        execute: workItem
      )
    } catch {
      client?.urlProtocol(self, didFailWithError: error)
    }
  }

  override func stopLoading() {
    workItem?.cancel()
    workItem = nil
  }
}
