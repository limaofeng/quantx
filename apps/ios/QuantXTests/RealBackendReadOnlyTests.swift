import Foundation
import XCTest

@testable import QuantX

@MainActor
final class RealBackendReadOnlyTests: XCTestCase {
  func testAuthenticatedRepositoriesAgainstExplicitDevelopmentBackend() async throws {
    let environment = ProcessInfo.processInfo.environment
    guard environment["QUANTX_IOS_ALLOW_DEVELOPMENT_LOGIN"] == "1",
      let rawBaseURL = environment["QUANTX_IOS_REAL_BACKEND_URL"],
      let baseURL = URL(string: rawBaseURL),
      let host = baseURL.host
    else {
      throw XCTSkip(
        "仅在显式提供真实开发后端并允许临时开发会话时运行"
      )
    }
    guard baseURL.scheme == "http", host == "127.0.0.1" || host == "localhost"
      || host.hasPrefix("10.") || host.hasPrefix("192.168.")
    else {
      return XCTFail("真实只读集成测试只允许 RFC1918 或本机 HTTP 开发地址")
    }

    let grant = try await createDevelopmentGrant(baseURL: baseURL)
    do {
      try await verifyReadOnlyRepositories(
        baseURL: baseURL,
        grant: grant
      )
    } catch {
      await deleteDevelopmentSession(
        baseURL: baseURL,
        accessToken: grant.accessToken
      )
      throw error
    }
    await deleteDevelopmentSession(
      baseURL: baseURL,
      accessToken: grant.accessToken
    )
  }

  private func verifyReadOnlyRepositories(
    baseURL: URL,
    grant: DevelopmentGrant
  ) async throws {
    let accountID = try XCTUnwrap(grant.user.authorizedAccountIds.first)
    let configuration = try makeConfiguration(baseURL: baseURL)
    let apollo = try ApolloClientFactory.make(
      configuration: configuration,
      accessToken: grant.accessToken
    )

    let portfolio = try await PortfolioRepository(client: apollo.client).load(
      authorizedAccountIDs: Set(grant.user.authorizedAccountIds)
    )
    guard case .snapshot(let portfolioSnapshot) = portfolio else {
      return XCTFail("显式开发账号应返回授权账户")
    }
    XCTAssertEqual(portfolioSnapshot.account.id, accountID)

    let strategies = try await StrategyRepository(client: apollo.client).load()
    XCTAssertFalse(strategies.instances.isEmpty)

    let trading = try await TradingActivityRepository(client: apollo.client).load(
      accountID: accountID
    )
    XCTAssertEqual(trading.accountID, accountID)

    let tTrade = try await TTradeAssistantRepository(client: apollo.client).load(
      accountID: accountID
    )
    XCTAssertEqual(tTrade.accountID, accountID)
    XCTAssertEqual(tTrade.holdingCount, tTrade.holdings.count)
  }

  private func createDevelopmentGrant(baseURL: URL) async throws -> DevelopmentGrant {
    let endpoint = baseURL.appending(path: "auth/web/session/development")
    var request = URLRequest(url: endpoint)
    request.httpMethod = "POST"
    request.setValue(baseURL.absoluteString, forHTTPHeaderField: "Origin")
    request.setValue("application/json", forHTTPHeaderField: "Accept")
    let (data, response) = try await URLSession.shared.data(for: request)
    let http = try XCTUnwrap(response as? HTTPURLResponse)
    XCTAssertEqual(http.statusCode, 200)
    return try JSONDecoder().decode(DevelopmentGrant.self, from: data)
  }

  private func deleteDevelopmentSession(baseURL: URL, accessToken: String) async {
    var request = URLRequest(url: baseURL.appending(path: "auth/session"))
    request.httpMethod = "DELETE"
    request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
    _ = try? await URLSession.shared.data(for: request)
  }

  private func makeConfiguration(baseURL: URL) throws -> APIConfiguration {
    let host = try XCTUnwrap(baseURL.host)
    let portSuffix = baseURL.port.map { ":\($0)" } ?? ""
    return APIConfiguration(
      environment: .debug,
      graphQLHTTPURL: try XCTUnwrap(
        URL(string: "http://\(host)\(portSuffix)/graphql")
      ),
      graphQLWebSocketURL: try XCTUnwrap(
        URL(string: "ws://\(host)\(portSuffix)/graphql")
      ),
      healthURL: baseURL.appending(path: "health"),
      authBaseURL: baseURL,
      accountDataEnabled: true
    )
  }
}

private struct DevelopmentGrant: Decodable {
  struct User: Decodable {
    let authorizedAccountIds: [String]
  }

  let accessToken: String
  let user: User
}
