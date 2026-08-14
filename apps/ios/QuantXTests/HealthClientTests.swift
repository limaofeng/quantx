import Foundation
import XCTest

@testable import QuantX

final class HealthClientTests: XCTestCase {
  override func setUp() {
    super.setUp()
    HealthURLProtocolStub.reset()
  }

  override func tearDown() {
    HealthURLProtocolStub.reset()
    super.tearDown()
  }

  func testHealthyResponseDecodesOnlyHealthSnapshot() async throws {
    HealthURLProtocolStub.install(
      statusCode: 200,
      body: Data(
        #"{"status":"healthy","version":"2.0.0","api_type":"GraphQL","environment":"development","realtime_enabled":true}"#
          .utf8
      )
    )
    let client = try makeClient()

    let snapshot = try await client.fetch()

    XCTAssertEqual(snapshot.status, "healthy")
    XCTAssertEqual(snapshot.version, "2.0.0")
    XCTAssertEqual(snapshot.apiType, "GraphQL")
    XCTAssertEqual(snapshot.realtimeEnabled, true)
    XCTAssertEqual(HealthURLProtocolStub.requestCount, 1)
  }

  func testCurrentReadyComponentResponseIsAccepted() async throws {
    HealthURLProtocolStub.install(
      statusCode: 200,
      body: Data(
        #"{"status":"ready","profile":"full","requiredComponents":["api","database"],"components":{"api":{"status":"ready"},"database":{"status":"ready"}}}"#
          .utf8
      )
    )
    let client = try makeClient()

    let snapshot = try await client.fetch()

    XCTAssertTrue(snapshot.isReady)
    XCTAssertEqual(snapshot.components["api"]?.isReady, true)
    XCTAssertNil(snapshot.realtimeEnabled)
  }

  func testHTTPFailureReturnsStatusWithoutResponseBody() async throws {
    HealthURLProtocolStub.install(
      statusCode: 503,
      body: Data("internal service details".utf8)
    )
    let client = try makeClient()

    do {
      _ = try await client.fetch()
      XCTFail("Expected HTTP status error")
    } catch HealthClient.HealthError.httpStatus(let code) {
      XCTAssertEqual(code, 503)
    } catch {
      XCTFail("Unexpected error: \(error)")
    }
  }

  func testMalformedSuccessPayloadUsesSafeInvalidResponseError() async throws {
    HealthURLProtocolStub.install(
      statusCode: 200,
      body: Data(#"{"databasePassword":"must-not-surface"}"#.utf8)
    )
    let client = try makeClient()

    do {
      _ = try await client.fetch()
      XCTFail("Expected invalid response")
    } catch HealthClient.HealthError.invalidResponse {
      XCTAssertEqual(
        HealthClient.HealthError.invalidResponse.errorDescription,
        "服务返回了无法识别的响应"
      )
    } catch {
      XCTFail("Unexpected error: \(error)")
    }
  }

  private func makeClient() throws -> HealthClient {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [HealthURLProtocolStub.self]
    return HealthClient(
      endpoint: try XCTUnwrap(URL(string: "https://quantx.test/health")),
      session: URLSession(configuration: configuration)
    )
  }
}

private final class HealthURLProtocolStub: URLProtocol, @unchecked Sendable {
  private static let lock = NSLock()
  nonisolated(unsafe) private static var statusCode = 200
  nonisolated(unsafe) private static var body = Data()
  nonisolated(unsafe) private static var requests: [URLRequest] = []

  static var requestCount: Int {
    lock.lock()
    defer { lock.unlock() }
    return requests.count
  }

  static func install(statusCode: Int, body: Data) {
    lock.lock()
    Self.statusCode = statusCode
    Self.body = body
    requests = []
    lock.unlock()
  }

  static func reset() {
    install(statusCode: 200, body: Data())
  }

  override class func canInit(with _: URLRequest) -> Bool {
    true
  }

  override class func canonicalRequest(for request: URLRequest) -> URLRequest {
    request
  }

  override func startLoading() {
    Self.lock.lock()
    let statusCode = Self.statusCode
    let body = Self.body
    Self.requests.append(request)
    Self.lock.unlock()

    guard let url = request.url,
      let response = HTTPURLResponse(
        url: url,
        statusCode: statusCode,
        httpVersion: "HTTP/1.1",
        headerFields: ["Content-Type": "application/json"]
      )
    else {
      client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
      return
    }
    client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
    client?.urlProtocol(self, didLoad: body)
    client?.urlProtocolDidFinishLoading(self)
  }

  override func stopLoading() {}
}
