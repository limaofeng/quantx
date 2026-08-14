import Apollo
import Foundation

@MainActor
protocol PortfolioLoading: AnyObject {
  func load(authorizedAccountIDs: Set<String>) async throws -> PortfolioLoadResult
}

@MainActor
final class PortfolioRepository: PortfolioLoading {
  enum RepositoryError: LocalizedError, Equatable {
    case unauthenticated
    case forbidden
    case accountScopeMismatch
    case invalidResponse
    case transport

    var errorDescription: String? {
      switch self {
      case .unauthenticated:
        "会话已失效，请重新登录"
      case .forbidden:
        "当前用户没有读取该账户的权限"
      case .accountScopeMismatch:
        "服务返回了授权范围之外的账户数据，已停止展示"
      case .invalidResponse:
        "账户服务返回了无法验证的数据"
      case .transport:
        "无法刷新账户数据，请检查私网或 VPN 连接"
      }
    }
  }

  private let client: ApolloClient

  init(client: ApolloClient) {
    self.client = client
  }

  func load(authorizedAccountIDs: Set<String>) async throws -> PortfolioLoadResult {
    do {
      let accountResponse = try await client.fetch(
        query: QuantXAPI.IOSCurrentAccountQuery(),
        cachePolicy: .networkOnly
      )
      try validate(errors: accountResponse.errors)
      guard let accountData = accountResponse.data else {
        throw RepositoryError.invalidResponse
      }
      let fetchedAt = Date()
      guard let graphQLAccount = accountData.currentAccount else {
        return .noAccount(fetchedAt: fetchedAt)
      }
      let account = try PortfolioAccount(graphQL: graphQLAccount)
      guard authorizedAccountIDs.contains(account.id) else {
        throw RepositoryError.accountScopeMismatch
      }

      let summaryResponse = try await client.fetch(
        query: QuantXAPI.IOSPortfolioSummaryQuery(accountId: .some(account.id)),
        cachePolicy: .networkOnly
      )
      try validate(errors: summaryResponse.errors)
      guard let graphQLSummary = summaryResponse.data?.portfolioSummary else {
        throw RepositoryError.invalidResponse
      }
      let metrics = try PortfolioMetrics(graphQL: graphQLSummary)
      guard metrics.accountID == account.id else {
        throw RepositoryError.accountScopeMismatch
      }

      let positionsResponse = try await client.fetch(
        query: QuantXAPI.IOSPositionsQuery(),
        cachePolicy: .networkOnly
      )
      try validate(errors: positionsResponse.errors)
      guard let graphQLPositions = positionsResponse.data?.positions else {
        throw RepositoryError.invalidResponse
      }
      let positions =
        try graphQLPositions
        .map(PortfolioPosition.init(graphQL:))
        .sorted {
          if ($0.marketValue ?? -.infinity) == ($1.marketValue ?? -.infinity) {
            return $0.stockCode < $1.stockCode
          }
          return ($0.marketValue ?? -.infinity) > ($1.marketValue ?? -.infinity)
        }
      guard positions.allSatisfy({ $0.accountID == account.id }) else {
        throw RepositoryError.accountScopeMismatch
      }

      return .snapshot(
        PortfolioSnapshot(
          account: account,
          metrics: metrics,
          positions: positions,
          fetchedAt: fetchedAt
        )
      )
    } catch is CancellationError {
      throw CancellationError()
    } catch let error as RepositoryError {
      throw error
    } catch is PortfolioMappingError {
      throw RepositoryError.invalidResponse
    } catch let error as ResponseCodeInterceptor.ResponseCodeError {
      switch error.response.statusCode {
      case 401:
        throw RepositoryError.unauthenticated
      case 403:
        throw RepositoryError.forbidden
      default:
        throw RepositoryError.transport
      }
    } catch {
      throw RepositoryError.transport
    }
  }

  private func validate(errors: [GraphQLError]?) throws {
    guard let errors, !errors.isEmpty else { return }
    let codes = Set(
      errors.compactMap { error in
        (error.extensions?["code"] as? String)?.uppercased()
      }
    )
    if codes.contains("UNAUTHENTICATED") {
      throw RepositoryError.unauthenticated
    }
    if codes.contains("FORBIDDEN") || codes.contains("PERMISSION_DENIED") {
      throw RepositoryError.forbidden
    }
    throw RepositoryError.invalidResponse
  }
}
