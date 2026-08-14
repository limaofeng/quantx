import Apollo
import Foundation

@MainActor
protocol TradingActivityLoading: AnyObject {
  func load(accountID: String) async throws -> TradingActivitySnapshot
}

@MainActor
final class TradingActivityRepository: TradingActivityLoading {
  private let client: ApolloClient
  private var shanghaiCalendar: Calendar {
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = TimeZone(identifier: "Asia/Shanghai") ?? .current
    return calendar
  }

  init(client: ApolloClient) {
    self.client = client
  }

  func load(accountID: String) async throws -> TradingActivitySnapshot {
    do {
      let endDate = Date()
      let startDate = shanghaiCalendar.date(byAdding: .day, value: -29, to: endDate) ?? endDate
      let startKey = dateKey(startDate)
      let endKey = dateKey(endDate)

      let todayOrdersResponse = try await client.fetch(
        query: QuantXAPI.IOSTodayOrdersQuery(accountId: accountID),
        cachePolicy: .networkOnly
      )
      try ApolloReadOnlyResponseValidator.validate(todayOrdersResponse.errors)
      guard let graphQLTodayOrders = todayOrdersResponse.data?.todayOrders else {
        throw ReadOnlyRepositoryError.invalidResponse
      }

      let todayTradesResponse = try await client.fetch(
        query: QuantXAPI.IOSTodayTradesQuery(accountId: accountID),
        cachePolicy: .networkOnly
      )
      try ApolloReadOnlyResponseValidator.validate(todayTradesResponse.errors)
      guard let graphQLTodayTrades = todayTradesResponse.data?.todayTrades else {
        throw ReadOnlyRepositoryError.invalidResponse
      }

      let historyOrdersResponse = try await client.fetch(
        query: QuantXAPI.IOSHistoryOrdersQuery(
          accountId: accountID,
          startDate: startKey,
          endDate: endKey
        ),
        cachePolicy: .networkOnly
      )
      try ApolloReadOnlyResponseValidator.validate(historyOrdersResponse.errors)
      guard let graphQLHistoryOrders = historyOrdersResponse.data?.historyOrders else {
        throw ReadOnlyRepositoryError.invalidResponse
      }

      let historyTradesResponse = try await client.fetch(
        query: QuantXAPI.IOSHistoryTradesQuery(
          accountId: accountID,
          startDate: startKey,
          endDate: endKey
        ),
        cachePolicy: .networkOnly
      )
      try ApolloReadOnlyResponseValidator.validate(historyTradesResponse.errors)
      guard let graphQLHistoryTrades = historyTradesResponse.data?.historyTrades else {
        throw ReadOnlyRepositoryError.invalidResponse
      }

      let todayOrders = try graphQLTodayOrders.map(OrderRecord.init(graphQL:)).sorted {
        $0.submittedAt > $1.submittedAt
      }
      let todayTrades = try graphQLTodayTrades.map(TradeRecord.init(graphQL:)).sorted {
        ($0.executedAt ?? .distantPast) > ($1.executedAt ?? .distantPast)
      }
      let historyOrders = try graphQLHistoryOrders.map(OrderRecord.init(graphQL:)).sorted {
        $0.submittedAt > $1.submittedAt
      }
      let historyTrades = try graphQLHistoryTrades.map(TradeRecord.init(graphQL:)).sorted {
        ($0.executedAt ?? .distantPast) > ($1.executedAt ?? .distantPast)
      }
      let allTrades = todayTrades + historyTrades
      guard allTrades.allSatisfy({ $0.accountID == accountID }) else {
        throw ReadOnlyRepositoryError.accountScopeMismatch
      }

      return TradingActivitySnapshot(
        accountID: accountID,
        todayOrders: todayOrders,
        todayTrades: todayTrades,
        historyOrders: historyOrders,
        historyTrades: historyTrades,
        historyStartDate: startDate,
        historyEndDate: endDate,
        fetchedAt: Date()
      )
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

  private func dateKey(_ date: Date) -> String {
    let components = shanghaiCalendar.dateComponents([.year, .month, .day], from: date)
    return String(
      format: "%04d-%02d-%02d",
      components.year ?? 0,
      components.month ?? 0,
      components.day ?? 0
    )
  }
}
