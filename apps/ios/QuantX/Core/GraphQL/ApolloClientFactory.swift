import Apollo
import ApolloWebSocket
import Foundation

struct ApolloSession: Sendable {
  let client: ApolloClient
  let webSocketTransport: WebSocketTransport

  func pauseSubscriptions() async {
    await webSocketTransport.pause()
  }

  func resumeSubscriptions() async {
    await webSocketTransport.resume()
  }

  func clearCache() async throws {
    try await client.clearCache()
  }
}

enum ApolloClientFactory {
  enum FactoryError: LocalizedError {
    case accountDataDisabled
    case missingAccessToken
    case secureTransportRequired

    var errorDescription: String? {
      switch self {
      case .accountDataDisabled:
        "后端认证授权尚未验收，账户数据连接保持关闭"
      case .missingAccessToken:
        "缺少访问令牌"
      case .secureTransportRequired:
        "账户数据只允许通过 HTTPS/WSS 连接"
      }
    }
  }

  static func make(
    configuration: APIConfiguration,
    accessToken: String
  ) throws -> ApolloSession {
    guard configuration.accountDataEnabled else {
      throw FactoryError.accountDataDisabled
    }
    guard !accessToken.isEmpty else {
      throw FactoryError.missingAccessToken
    }
    let usesSecureTransport =
      configuration.graphQLHTTPURL.scheme?.lowercased() == "https"
      && configuration.graphQLWebSocketURL.scheme?.lowercased() == "wss"
    let usesDevelopmentTransport =
      configuration.environment == .debug
      && configuration.graphQLHTTPURL.scheme?.lowercased() == "http"
      && configuration.graphQLWebSocketURL.scheme?.lowercased() == "ws"
    guard usesSecureTransport || usesDevelopmentTransport else {
      throw FactoryError.secureTransportRequired
    }

    let store = ApolloStore(cache: InMemoryNormalizedCache())
    let authorization = "Bearer \(accessToken)"
    let httpSession = URLSession(configuration: .ephemeral)
    let httpTransport = RequestChainNetworkTransport(
      urlSession: httpSession,
      interceptorProvider: DefaultInterceptorProvider.shared,
      store: store,
      endpointURL: configuration.graphQLHTTPURL,
      additionalHeaders: ["Authorization": authorization]
    )

    let webSocketSession = URLSession(configuration: .ephemeral)
    let webSocketTransport = try WebSocketTransport(
      urlSession: webSocketSession,
      store: store,
      endpointURL: configuration.graphQLWebSocketURL,
      configuration: .init(
        reconnectionInterval: -1,
        connectingPayload: ["Authorization": authorization]
      )
    )
    let splitTransport = SplitNetworkTransport(
      queryTransport: httpTransport,
      mutationTransport: httpTransport,
      subscriptionTransport: webSocketTransport
    )
    let client = ApolloClient(networkTransport: splitTransport, store: store)

    return ApolloSession(client: client, webSocketTransport: webSocketTransport)
  }
}
