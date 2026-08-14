import Foundation
import XCTest

@testable import QuantX

final class APIConfigurationTests: XCTestCase {
  func testDebugConfigurationSupportsRealAccountDataOverLocalHTTP() throws {
    let configuration = APIConfiguration(
      environment: .debug,
      graphQLHTTPURL: try XCTUnwrap(URL(string: "http://192.168.5.6:8080/graphql")),
      graphQLWebSocketURL: try XCTUnwrap(URL(string: "ws://192.168.5.6:8080/graphql")),
      healthURL: try XCTUnwrap(URL(string: "http://192.168.5.6:8080/health")),
      authBaseURL: try XCTUnwrap(URL(string: "http://192.168.5.6:8080")),
      accountDataEnabled: true
    )

    XCTAssertEqual(configuration.serviceHost, "192.168.5.6")
    XCTAssertTrue(configuration.accountDataEnabled)
    XCTAssertTrue(configuration.usesInsecureAccountTransport)
  }

  func testApolloFactoryRefusesDisabledAccountDataBeforeOpeningConnection() throws {
    let configuration = APIConfiguration(
      environment: .debug,
      graphQLHTTPURL: try XCTUnwrap(URL(string: "http://192.168.5.6:8080/graphql")),
      graphQLWebSocketURL: try XCTUnwrap(URL(string: "ws://192.168.5.6:8080/graphql")),
      healthURL: try XCTUnwrap(URL(string: "http://192.168.5.6:8080/health")),
      authBaseURL: try XCTUnwrap(URL(string: "http://192.168.5.6:8080")),
      accountDataEnabled: false
    )

    XCTAssertThrowsError(
      try ApolloClientFactory.make(configuration: configuration, accessToken: "unused")
    ) { error in
      guard case ApolloClientFactory.FactoryError.accountDataDisabled = error else {
        return XCTFail("Expected accountDataDisabled, got \(error)")
      }
    }
  }

  func testSessionClientRefusesCredentialsOverHTTPWithoutDevelopmentOptIn() throws {
    XCTAssertThrowsError(
      try SessionClient(baseURL: XCTUnwrap(URL(string: "http://192.168.5.6:8080")))
    ) { error in
      guard case SessionClient.ClientError.secureTransportRequired = error else {
        return XCTFail("Expected secureTransportRequired, got \(error)")
      }
    }
  }

  func testSessionClientAllowsHTTPForDevelopment() throws {
    XCTAssertNoThrow(
      try SessionClient(
        baseURL: XCTUnwrap(URL(string: "http://192.168.5.6:8080")),
        allowsInsecureDevelopmentTransport: true
      )
    )
  }

  func testApolloFactoryAllowsHTTPAndWebSocketForDebugAccountData() throws {
    let configuration = APIConfiguration(
      environment: .debug,
      graphQLHTTPURL: try XCTUnwrap(URL(string: "http://192.168.5.6:8080/graphql")),
      graphQLWebSocketURL: try XCTUnwrap(URL(string: "ws://192.168.5.6:8080/graphql")),
      healthURL: try XCTUnwrap(URL(string: "http://192.168.5.6:8080/health")),
      authBaseURL: try XCTUnwrap(URL(string: "http://192.168.5.6:8080")),
      accountDataEnabled: true
    )

    XCTAssertNoThrow(
      try ApolloClientFactory.make(configuration: configuration, accessToken: "access-token")
    )
  }
}
