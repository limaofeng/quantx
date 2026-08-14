import Apollo
import Foundation

@MainActor
protocol MarketDataLoading: AnyObject {
  func loadWatchlist(
    accountID: String,
    authorizedAccountIDs: Set<String>
  ) async throws -> MarketWorkspaceSnapshot
  func search(term: String) async throws -> [MarketInstrument]
  func loadInstrument(
    stockCode: String,
    period: MarketPeriod
  ) async throws -> MarketInstrumentSnapshot?
  func quoteUpdates(stockCode: String) throws -> AsyncThrowingStream<MarketLiveQuote, any Error>
  func depthUpdates(stockCode: String) throws
    -> AsyncThrowingStream<MarketDepthSnapshot, any Error>
}

@MainActor
final class MarketRepository: MarketDataLoading {
  private let client: ApolloClient

  init(client: ApolloClient) {
    self.client = client
  }

  func loadWatchlist(
    accountID: String,
    authorizedAccountIDs: Set<String>
  ) async throws -> MarketWorkspaceSnapshot {
    guard authorizedAccountIDs.contains(accountID) else {
      throw ReadOnlyRepositoryError.accountScopeMismatch
    }
    do {
      let response = try await client.fetch(
        query: QuantXAPI.IOSMarketWatchlistQuery(accountId: .some(accountID)),
        cachePolicy: .networkOnly
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let data = response.data else {
        throw ReadOnlyRepositoryError.invalidResponse
      }

      let items = try data.watchlist.map { item in
        try ReadOnlyModelValidator.requireNonempty(item.id, field: "market.watchlist.id")
        try ReadOnlyModelValidator.requireNonempty(
          item.stockCode,
          field: "market.watchlist.stockCode"
        )
        guard item.accountId == accountID, authorizedAccountIDs.contains(item.accountId),
          item.displayOrder >= 0
        else {
          throw ReadOnlyRepositoryError.accountScopeMismatch
        }
        return MarketWatchItem(
          id: item.id,
          accountID: item.accountId,
          stockCode: item.stockCode,
          instrumentName: item.instrumentName,
          displayOrder: item.displayOrder,
          groupName: item.groupName,
          note: item.note,
          updatedAt: item.updatedAt.flatMap(PortfolioDateParser.parse),
          quote: nil
        )
      }

      let quotes = try await loadLatestQuotes(stockCodes: items.map(\.stockCode))
      let quotesByCode = Dictionary(uniqueKeysWithValues: quotes.map { ($0.stockCode, $0) })
      let hydrated = items.map { item in
        MarketWatchItem(
          id: item.id,
          accountID: item.accountID,
          stockCode: item.stockCode,
          instrumentName: item.instrumentName,
          displayOrder: item.displayOrder,
          groupName: item.groupName,
          note: item.note,
          updatedAt: item.updatedAt,
          quote: quotesByCode[item.stockCode]
        )
      }
      .sorted {
        if $0.displayOrder == $1.displayOrder {
          return $0.stockCode < $1.stockCode
        }
        return $0.displayOrder < $1.displayOrder
      }
      return MarketWorkspaceSnapshot(
        accountID: accountID,
        watchlist: hydrated,
        fetchedAt: Date()
      )
    } catch {
      throw map(error)
    }
  }

  func search(term: String) async throws -> [MarketInstrument] {
    let normalized = term.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !normalized.isEmpty else { return [] }
    do {
      let response = try await client.fetch(
        query: QuantXAPI.IOSMarketSearchQuery(term: normalized),
        cachePolicy: .networkOnly
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let data = response.data else {
        throw ReadOnlyRepositoryError.invalidResponse
      }

      let byCode = try data.byCode.map(mapSearchByCode)
      let byName = try data.byName.map(mapSearchByName)
      var unique: [String: MarketInstrument] = [:]
      for item in byCode + byName where unique[item.id] == nil {
        unique[item.id] = item
      }
      return unique.values.sorted {
        $0.stockCode.localizedStandardCompare($1.stockCode) == .orderedAscending
      }
    } catch {
      throw map(error)
    }
  }

  func loadInstrument(
    stockCode: String,
    period: MarketPeriod
  ) async throws -> MarketInstrumentSnapshot? {
    let normalized = stockCode.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !normalized.isEmpty else {
      throw ReadOnlyRepositoryError.invalidResponse
    }
    do {
      let response = try await client.fetch(
        query: QuantXAPI.IOSMarketInstrumentDetailQuery(
          stockCode: normalized,
          period: .case(period.graphQLValue)
        ),
        cachePolicy: .networkOnly
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let data = response.data else {
        throw ReadOnlyRepositoryError.invalidResponse
      }
      guard let value = data.instrument else { return nil }
      let instrument = try mapDetailInstrument(value)
      let validCodes = Set([normalized, instrument.stockCode, instrument.instrumentID])
      let candles = try data.klines.map { value in
        try ReadOnlyModelValidator.requireNonempty(value.stockCode, field: "market.kline.stockCode")
        guard validCodes.contains(value.stockCode), value.volume >= 0 else {
          throw ReadOnlyRepositoryError.invalidResponse
        }
        try ReadOnlyModelValidator.requireFinite(
          [value.open, value.high, value.low, value.close, value.preClose, value.amount],
          field: "market.kline.values"
        )
        guard value.open >= 0, value.high >= 0, value.low >= 0, value.close >= 0,
          value.preClose >= 0, value.amount >= 0
        else {
          throw ReadOnlyRepositoryError.invalidResponse
        }
        return MarketCandle(
          stockCode: value.stockCode,
          period: value.period,
          time: try ReadOnlyModelValidator.requireDate(value.time, field: "market.kline.time"),
          open: value.open,
          high: value.high,
          low: value.low,
          close: value.close,
          previousClose: value.preClose,
          volume: value.volume,
          amount: value.amount
        )
      }
      return MarketInstrumentSnapshot(
        instrument: instrument,
        period: period,
        candles: candles,
        fetchedAt: Date()
      )
    } catch {
      throw map(error)
    }
  }

  func quoteUpdates(
    stockCode: String
  ) throws -> AsyncThrowingStream<MarketLiveQuote, any Error> {
    let source = try client.subscribe(
      subscription: QuantXAPI.IOSMarketQuoteUpdatesSubscription(stockList: [stockCode]),
      cachePolicy: .networkOnly
    )
    return AsyncThrowingStream { continuation in
      let task = Task { @MainActor in
        do {
          for try await response in source {
            try ApolloReadOnlyResponseValidator.validate(response.errors)
            guard let value = response.data?.marketQuotes, value.stockCode == stockCode else {
              throw ReadOnlyRepositoryError.invalidResponse
            }
            try ReadOnlyModelValidator.requireFinite(
              [
                value.currentPrice, value.volume, value.amount, value.bidPrice, value.askPrice,
                value.high, value.low, value.open,
              ] + [value.change, value.changePercent, value.preClose].compactMap { $0 },
              field: "market.liveQuote.values"
            )
            guard value.bidVolume >= 0, value.askVolume >= 0 else {
              throw ReadOnlyRepositoryError.invalidResponse
            }
            continuation.yield(
              MarketLiveQuote(
                stockCode: value.stockCode,
                time: try ReadOnlyModelValidator.requireDate(
                  value.time,
                  field: "market.liveQuote.time"
                ),
                currentPrice: value.currentPrice,
                change: value.change,
                changePercent: value.changePercent,
                volume: value.volume,
                amount: value.amount,
                bidPrice: value.bidPrice,
                askPrice: value.askPrice,
                bidVolume: value.bidVolume,
                askVolume: value.askVolume,
                high: value.high,
                low: value.low,
                open: value.open,
                previousClose: value.preClose
              )
            )
          }
          continuation.finish()
        } catch is CancellationError {
          continuation.finish()
        } catch {
          continuation.finish(throwing: map(error))
        }
      }
      continuation.onTermination = { _ in task.cancel() }
    }
  }

  func depthUpdates(
    stockCode: String
  ) throws -> AsyncThrowingStream<MarketDepthSnapshot, any Error> {
    let source = try client.subscribe(
      subscription: QuantXAPI.IOSMarketDepthUpdatesSubscription(
        stockList: [stockCode],
        levels: 5
      ),
      cachePolicy: .networkOnly
    )
    return AsyncThrowingStream { continuation in
      let task = Task { @MainActor in
        do {
          for try await response in source {
            try ApolloReadOnlyResponseValidator.validate(response.errors)
            guard let value = response.data?.marketDepth, value.stockCode == stockCode else {
              throw ReadOnlyRepositoryError.invalidResponse
            }
            let bids = try value.bids.map { level in
              try mapDepthLevel(price: level.price, volume: level.volume)
            }
            let asks = try value.asks.map { level in
              try mapDepthLevel(price: level.price, volume: level.volume)
            }
            continuation.yield(
              MarketDepthSnapshot(
                stockCode: value.stockCode,
                time: try ReadOnlyModelValidator.requireDate(
                  value.time,
                  field: "market.depth.time"
                ),
                bids: bids,
                asks: asks
              )
            )
          }
          continuation.finish()
        } catch is CancellationError {
          continuation.finish()
        } catch {
          continuation.finish(throwing: map(error))
        }
      }
      continuation.onTermination = { _ in task.cancel() }
    }
  }

  private func loadLatestQuotes(stockCodes: [String]) async throws -> [MarketQuote] {
    guard !stockCodes.isEmpty else { return [] }
    let response = try await client.fetch(
      query: QuantXAPI.IOSLatestMarketQuotesQuery(stockList: stockCodes),
      cachePolicy: .networkOnly
    )
    try ApolloReadOnlyResponseValidator.validate(response.errors)
    guard let values = response.data?.latestMarketQuotes else {
      throw ReadOnlyRepositoryError.invalidResponse
    }
    let requested = Set(stockCodes)
    return try values.map { value in
      guard requested.contains(value.stockCode) else {
        throw ReadOnlyRepositoryError.invalidResponse
      }
      return try MarketMapping.quote(
        stockCode: value.stockCode,
        time: value.time,
        lastPrice: value.lastPrice,
        open: value.open,
        high: value.high,
        low: value.low,
        preClose: value.preClose,
        change: value.change,
        changePercent: value.changePercent,
        volume: value.volume,
        amount: value.amount,
        turnoverRate: value.turnoverRate
      )
    }
  }

  private func mapSearchByCode(
    _ value: QuantXAPI.IOSMarketSearchQuery.Data.ByCode
  ) throws -> MarketInstrument {
    let quote = try value.quote.map { quote in
      try MarketMapping.quote(
        stockCode: quote.stockCode,
        time: quote.time,
        lastPrice: quote.lastPrice,
        open: quote.open,
        high: quote.high,
        low: quote.low,
        preClose: quote.preClose,
        change: quote.change,
        changePercent: quote.changePercent,
        volume: quote.volume,
        amount: quote.amount,
        turnoverRate: quote.turnoverRate
      )
    }
    return try MarketMapping.instrument(
      stockCode: value.id,
      market: value.market,
      instrumentID: value.instrumentId,
      name: value.name,
      abbreviation: value.abbreviation,
      exchangeCode: value.exchangeCode,
      previousClose: value.preClose,
      upperLimit: value.upStopPrice,
      lowerLimit: value.downStopPrice,
      priceTick: value.priceTick,
      isTrading: value.isTrading,
      quote: quote
    )
  }

  private func mapSearchByName(
    _ value: QuantXAPI.IOSMarketSearchQuery.Data.ByName
  ) throws -> MarketInstrument {
    let quote = try value.quote.map { quote in
      try MarketMapping.quote(
        stockCode: quote.stockCode,
        time: quote.time,
        lastPrice: quote.lastPrice,
        open: quote.open,
        high: quote.high,
        low: quote.low,
        preClose: quote.preClose,
        change: quote.change,
        changePercent: quote.changePercent,
        volume: quote.volume,
        amount: quote.amount,
        turnoverRate: quote.turnoverRate
      )
    }
    return try MarketMapping.instrument(
      stockCode: value.id,
      market: value.market,
      instrumentID: value.instrumentId,
      name: value.name,
      abbreviation: value.abbreviation,
      exchangeCode: value.exchangeCode,
      previousClose: value.preClose,
      upperLimit: value.upStopPrice,
      lowerLimit: value.downStopPrice,
      priceTick: value.priceTick,
      isTrading: value.isTrading,
      quote: quote
    )
  }

  private func mapDetailInstrument(
    _ value: QuantXAPI.IOSMarketInstrumentDetailQuery.Data.Instrument
  ) throws -> MarketInstrument {
    let quote = try value.quote.map { quote in
      try MarketMapping.quote(
        stockCode: quote.stockCode,
        time: quote.time,
        lastPrice: quote.lastPrice,
        open: quote.open,
        high: quote.high,
        low: quote.low,
        preClose: quote.preClose,
        change: quote.change,
        changePercent: quote.changePercent,
        volume: quote.volume,
        amount: quote.amount,
        turnoverRate: quote.turnoverRate
      )
    }
    return try MarketMapping.instrument(
      stockCode: value.id,
      market: value.market,
      instrumentID: value.instrumentId,
      name: value.name,
      abbreviation: value.abbreviation,
      exchangeCode: value.exchangeCode,
      previousClose: value.preClose,
      upperLimit: value.upStopPrice,
      lowerLimit: value.downStopPrice,
      priceTick: value.priceTick,
      isTrading: value.isTrading,
      quote: quote
    )
  }

  private func mapDepthLevel(price: Double, volume: Int) throws -> MarketDepthLevel {
    try ReadOnlyModelValidator.requireFinite([price], field: "market.depth.price")
    guard price >= 0, volume >= 0 else {
      throw ReadOnlyMappingError.invalidField("market.depth.level")
    }
    return MarketDepthLevel(price: price, volume: volume)
  }

  private func map(_ error: Error) -> Error {
    if error is CancellationError { return CancellationError() }
    if let error = error as? ReadOnlyRepositoryError { return error }
    if error is ReadOnlyMappingError { return ReadOnlyRepositoryError.invalidResponse }
    if let error = error as? ResponseCodeInterceptor.ResponseCodeError {
      return ApolloReadOnlyResponseValidator.mapResponseCode(error)
    }
    return ReadOnlyRepositoryError.transport
  }
}
