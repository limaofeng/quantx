import Apollo
import Foundation

@MainActor
protocol StrategyMonitoringLoading: AnyObject {
  func load() async throws -> StrategyMonitorSnapshot
}

enum ReadOnlyRepositoryError: LocalizedError, Equatable {
  case unauthenticated
  case forbidden
  case accountScopeMismatch
  case invalidResponse
  case transport
  case graphQL(code: String, requestID: String?)

  var errorDescription: String? {
    switch self {
    case .unauthenticated:
      "会话已失效，请重新登录"
    case .forbidden:
      "当前用户没有读取此数据的权限"
    case .accountScopeMismatch:
      "服务返回了授权范围之外的账户数据，已停止展示"
    case .invalidResponse:
      "服务返回了无法验证的数据"
    case .transport:
      "无法刷新数据，请检查私网或 VPN 连接"
    case .graphQL(let code, let requestID):
      if let requestID, !requestID.isEmpty {
        "服务端拒绝了数据请求（\(code)），请求 ID：\(requestID)"
      } else {
        "服务端拒绝了数据请求（\(code)）"
      }
    }
  }
}

enum ApolloReadOnlyResponseValidator {
  static func validate(_ errors: [GraphQLError]?) throws {
    guard let errors, !errors.isEmpty else { return }
    let codes = Set(
      errors.compactMap { error in
        (error.extensions?["code"] as? String)?.uppercased()
      }
    )
    if codes.contains("UNAUTHENTICATED") {
      throw ReadOnlyRepositoryError.unauthenticated
    }
    if codes.contains("FORBIDDEN") || codes.contains("PERMISSION_DENIED") {
      throw ReadOnlyRepositoryError.forbidden
    }
    let first = errors[0]
    let code = (first.extensions?["code"] as? String)?.uppercased() ?? "GRAPHQL_ERROR"
    let requestID = first.extensions?["requestId"] as? String
    throw ReadOnlyRepositoryError.graphQL(code: code, requestID: requestID)
  }

  static func mapResponseCode(_ error: ResponseCodeInterceptor.ResponseCodeError) -> Error {
    switch error.response.statusCode {
    case 401:
      ReadOnlyRepositoryError.unauthenticated
    case 403:
      ReadOnlyRepositoryError.forbidden
    default:
      ReadOnlyRepositoryError.transport
    }
  }
}

@MainActor
final class StrategyRepository: StrategyMonitoringLoading {
  private let client: ApolloClient

  init(client: ApolloClient) {
    self.client = client
  }

  func load() async throws -> StrategyMonitorSnapshot {
    do {
      let response = try await client.fetch(
        query: QuantXAPI.IOSStrategyInstancesQuery(),
        cachePolicy: .networkOnly
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let graphQLInstances = response.data?.strategyInstances else {
        throw ReadOnlyRepositoryError.invalidResponse
      }
      let instances = try graphQLInstances
        .map(StrategyMonitorItem.init(graphQL:))
        .sorted {
          if $0.updatedAt == $1.updatedAt {
            return $0.displayName.localizedStandardCompare($1.displayName) == .orderedAscending
          }
          return $0.updatedAt > $1.updatedAt
        }
      return StrategyMonitorSnapshot(instances: instances, fetchedAt: Date())
    } catch is CancellationError {
      throw CancellationError()
    } catch let error as ReadOnlyRepositoryError {
      throw error
    } catch is ReadOnlyMappingError {
      throw ReadOnlyRepositoryError.invalidResponse
    } catch let error as ResponseCodeInterceptor.ResponseCodeError {
      throw ApolloReadOnlyResponseValidator.mapResponseCode(error)
    } catch {
      throw ReadOnlyRepositoryError.transport
    }
  }
}
