import Charts
import SwiftUI

struct MarketWorkspaceView: View {
  private struct Route: Hashable {
    let stockCode: String
    let displayName: String
  }

  @EnvironmentObject private var model: AppModel
  @State private var searchText = ""
  @State private var searchState: MarketSearchState = .idle
  @State private var searchTask: Task<Void, Never>?

  var body: some View {
    NavigationStack {
      ScrollView {
        LazyVStack(alignment: .leading, spacing: QuantXTheme.Spacing.large) {
          if searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            workspaceContent
          } else {
            searchContent
          }
        }
        .padding(.horizontal, QuantXTheme.Spacing.large)
        .padding(.top, QuantXTheme.Spacing.small)
        .padding(.bottom, QuantXTheme.Spacing.xLarge)
      }
      .background(QuantXTheme.canvasBackground)
      .navigationTitle("行情")
      .searchable(
        text: $searchText,
        placement: .navigationBarDrawer(displayMode: .always),
        prompt: "输入股票代码或名称"
      )
      .textInputAutocapitalization(.characters)
      .autocorrectionDisabled()
      .navigationDestination(for: Route.self) { route in
        MarketInstrumentDetailView(
          stockCode: route.stockCode,
          initialDisplayName: route.displayName
        )
      }
      .toolbar {
        ToolbarItem(placement: .topBarTrailing) {
          Button {
            Task { await model.refreshMarket() }
          } label: {
            if model.marketRefreshInProgress {
              ProgressView()
            } else {
              Image(systemName: "arrow.clockwise")
            }
          }
          .frame(minWidth: 44, minHeight: 44)
          .disabled(model.marketRefreshInProgress)
          .accessibilityLabel(model.marketRefreshInProgress ? "正在刷新行情" : "刷新行情")
        }
      }
      .onChange(of: searchText) { _, value in
        scheduleSearch(value)
      }
      .onSubmit(of: .search) {
        searchTask?.cancel()
        Task { await search(searchText) }
      }
      .refreshable {
        await model.refreshMarket()
      }
    }
    .task {
      if case .idle = model.marketState {
        await model.refreshMarket()
      }
    }
    .onDisappear {
      searchTask?.cancel()
    }
  }

  @ViewBuilder
  private var workspaceContent: some View {
    marketContextHeader

    switch model.marketState {
    case .unavailable(let reason):
      unavailableCard(
        title: "行情服务不可用",
        message: reason,
        systemImage: "chart.xyaxis.line"
      )
    case .idle, .loading:
      loadingCard
    case .noAccount:
      unavailableCard(
        title: "没有可用账户",
        message: "当前会话没有可用于自选与持仓行情的主账户。",
        systemImage: "person.crop.circle.badge.questionmark"
      )
    case .loaded(let snapshot, let refreshWarning):
      if let refreshWarning {
        RefreshWarningView(message: refreshWarning)
      }
      watchlist(snapshot)
      holdings
      HStack {
        DataFreshnessView(updatedAt: snapshot.sourceUpdatedAt)
        Spacer()
        Text(snapshot.fetchedAt.formatted(date: .omitted, time: .standard))
          .font(.caption)
          .foregroundStyle(QuantXTheme.secondaryText)
          .monospacedDigit()
      }
    case .failed(let message):
      unavailableCard(
        title: "无法读取行情",
        message: message,
        systemImage: "wifi.exclamationmark",
        retry: { Task { await model.refreshMarket() } }
      )
    }
  }

  private var marketContextHeader: some View {
    QuantXStatusBanner(
      title: "A 股行情工作台",
      message: "搜索、自选、持仓行情与 K 线均来自 QuantX 服务；价格时间会明确标注。",
      status: .ready
    )
  }

  private var loadingCard: some View {
    QuantXCard {
      HStack(spacing: QuantXTheme.Spacing.medium) {
        ProgressView()
        VStack(alignment: .leading, spacing: 4) {
          Text("正在读取自选与行情")
            .font(.subheadline.weight(.semibold))
          Text("不会使用本地模拟报价")
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        }
      }
      .frame(minHeight: 72)
      .accessibilityElement(children: .combine)
    }
  }

  @ViewBuilder
  private var searchContent: some View {
    SectionTitle(title: "搜索结果", subtitle: "证券代码与名称")

    switch searchState {
    case .idle, .loading:
      QuantXCard {
        HStack(spacing: 12) {
          ProgressView()
          Text("正在搜索真实证券目录…")
            .font(.subheadline)
        }
        .frame(minHeight: 52)
      }
    case .loaded(let instruments):
      if instruments.isEmpty {
        unavailableCard(
          title: "没有匹配标的",
          message: "请检查证券代码或名称后重试。",
          systemImage: "magnifyingglass"
        )
      } else {
        ForEach(instruments) { instrument in
          NavigationLink(
            value: Route(
              stockCode: instrument.id,
              displayName: instrument.displayName
            )
          ) {
            MarketInstrumentRow(instrument: instrument)
          }
          .buttonStyle(.plain)
          .accessibilityHint("查看行情、K线和五档盘口")
        }
      }
    case .failed(let message):
      unavailableCard(
        title: "搜索失败",
        message: message,
        systemImage: "wifi.exclamationmark",
        retry: { Task { await search(searchText) } }
      )
    }
  }

  private func watchlist(_ snapshot: MarketWorkspaceSnapshot) -> some View {
    VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
      SectionTitle(title: "自选", subtitle: "按个人账户排序") {
        Text("\(snapshot.watchlist.count) 只")
          .font(.subheadline)
          .foregroundStyle(QuantXTheme.secondaryText)
          .monospacedDigit()
      }

      if snapshot.watchlist.isEmpty {
        unavailableCard(
          title: "自选列表为空",
          message: "可先搜索证券查看详情；自选维护需独立写权限开放后启用。",
          systemImage: "star"
        )
      } else {
        ForEach(snapshot.watchlist) { item in
          NavigationLink(
            value: Route(stockCode: item.stockCode, displayName: item.displayName)
          ) {
            MarketWatchlistRow(item: item)
          }
          .buttonStyle(.plain)
          .accessibilityHint("查看行情、K线和五档盘口")
        }
      }
    }
  }

  @ViewBuilder
  private var holdings: some View {
    if let snapshot = model.portfolioState.snapshot, !snapshot.positions.isEmpty {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(title: "持仓行情", subtitle: "账户持仓中的真实价格快照") {
          Button("查看资产") {
            model.selectedTab = .assets
          }
          .font(.subheadline.weight(.semibold))
          .frame(minHeight: 44)
        }
        ForEach(snapshot.positions.prefix(6)) { position in
          NavigationLink(
            value: Route(stockCode: position.stockCode, displayName: position.displayName)
          ) {
            QuantXCard {
              HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 3) {
                  Text(position.displayName)
                    .font(.headline)
                    .foregroundStyle(.primary)
                  Text(position.stockCode)
                    .font(.caption)
                    .foregroundStyle(QuantXTheme.secondaryText)
                    .monospacedDigit()
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 3) {
                  Text(PortfolioFormatters.decimal(position.lastPrice))
                    .font(.headline)
                    .foregroundStyle(QuantXTheme.trendColor(position.profitLoss))
                    .monospacedDigit()
                  Text(PortfolioFormatters.signedPercentage(position.profitRate))
                    .font(.caption)
                    .foregroundStyle(QuantXTheme.trendColor(position.profitLoss))
                    .monospacedDigit()
                }
              }
              .accessibilityElement(children: .combine)
            }
          }
          .buttonStyle(.plain)
        }
      }
    }
  }

  private func unavailableCard(
    title: String,
    message: String,
    systemImage: String,
    retry: (() -> Void)? = nil
  ) -> some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        Label(title, systemImage: systemImage)
          .font(.headline)
        Text(message)
          .font(.subheadline)
          .foregroundStyle(QuantXTheme.secondaryText)
          .fixedSize(horizontal: false, vertical: true)
        if let retry {
          Button("重新加载", action: retry)
            .buttonStyle(.borderedProminent)
        }
      }
    }
  }

  private func scheduleSearch(_ term: String) {
    searchTask?.cancel()
    let normalized = term.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !normalized.isEmpty else {
      searchState = .idle
      return
    }
    searchState = .loading
    searchTask = Task {
      try? await Task.sleep(for: .milliseconds(320))
      guard !Task.isCancelled else { return }
      await search(normalized)
    }
  }

  private func search(_ term: String) async {
    let normalized = term.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !normalized.isEmpty else {
      searchState = .idle
      return
    }
    searchState = .loading
    do {
      searchState = .loaded(try await model.searchMarket(term: normalized))
    } catch is CancellationError {
      return
    } catch {
      searchState = .failed(
        (error as? LocalizedError)?.errorDescription ?? "证券搜索暂时不可用"
      )
    }
  }
}

private struct MarketWatchlistRow: View {
  let item: MarketWatchItem

  var body: some View {
    QuantXCard {
      HStack(alignment: .firstTextBaseline, spacing: 12) {
        VStack(alignment: .leading, spacing: 4) {
          Text(item.displayName)
            .font(.headline)
          Text(item.stockCode)
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
            .monospacedDigit()
          if let groupName = item.groupName, !groupName.isEmpty {
            Text(groupName)
              .font(.caption2)
              .foregroundStyle(QuantXTheme.secondaryText)
          }
        }
        Spacer(minLength: 8)
        if let quote = item.quote {
          VStack(alignment: .trailing, spacing: 4) {
            Text(PortfolioFormatters.decimal(quote.lastPrice))
              .font(.headline)
              .monospacedDigit()
            Text(PortfolioFormatters.signedPercentage(quote.changePercent))
              .font(.subheadline.weight(.semibold))
              .monospacedDigit()
            Text(quote.time.formatted(date: .omitted, time: .standard))
              .font(.caption2)
              .foregroundStyle(QuantXTheme.secondaryText)
              .monospacedDigit()
          }
          .foregroundStyle(QuantXTheme.trendColor(quote.trend))
        } else {
          StatusBadge(
            title: "暂无报价",
            systemImage: "clock.badge.questionmark",
            color: QuantXTheme.secondaryText
          )
        }
      }
      .accessibilityElement(children: .combine)
    }
  }
}

private struct MarketInstrumentRow: View {
  let instrument: MarketInstrument

  var body: some View {
    QuantXCard {
      HStack(alignment: .firstTextBaseline, spacing: 12) {
        VStack(alignment: .leading, spacing: 4) {
          Text(instrument.displayName)
            .font(.headline)
            .foregroundStyle(.primary)
          Text(instrument.stockCode)
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
            .monospacedDigit()
        }
        Spacer()
        if let quote = instrument.quote {
          VStack(alignment: .trailing, spacing: 3) {
            Text(PortfolioFormatters.decimal(quote.lastPrice))
              .font(.headline)
            Text(PortfolioFormatters.signedPercentage(quote.changePercent))
              .font(.caption.weight(.semibold))
          }
          .foregroundStyle(QuantXTheme.trendColor(quote.trend))
          .monospacedDigit()
        } else {
          Text(instrument.isTrading == false ? "不可交易" : "暂无报价")
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        }
      }
      .accessibilityElement(children: .combine)
    }
  }
}

private struct MarketInstrumentDetailView: View {
  @EnvironmentObject private var model: AppModel

  let stockCode: String
  let initialDisplayName: String

  @State private var period: MarketPeriod = .day
  @State private var state: MarketInstrumentState = .idle
  @State private var liveQuote: MarketLiveQuote?
  @State private var depth: MarketDepthSnapshot?
  @State private var streamWarning: String?

  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: QuantXTheme.Spacing.large) {
        content
      }
      .padding(QuantXTheme.Spacing.large)
    }
    .background(QuantXTheme.canvasBackground)
    .navigationTitle(displayName)
    .navigationBarTitleDisplayMode(.inline)
    .task(id: period) {
      await load()
    }
    .task(id: "quote-\(stockCode)") {
      await consumeQuoteUpdates()
    }
    .task(id: "depth-\(stockCode)") {
      await consumeDepthUpdates()
    }
    .refreshable {
      await load()
    }
  }

  @ViewBuilder
  private var content: some View {
    switch state {
    case .idle, .loading:
      ProgressView("正在读取行情与 K 线…")
        .frame(maxWidth: .infinity, minHeight: 240)
    case .notFound:
      ContentUnavailableView {
        Label("未找到证券", systemImage: "magnifyingglass")
      } description: {
        Text("服务端没有返回 \(stockCode) 的证券资料。")
      }
    case .failed(let message):
      ContentUnavailableView {
        Label("无法读取证券行情", systemImage: "wifi.exclamationmark")
      } description: {
        Text(message)
      } actions: {
        Button("重新加载") { Task { await load() } }
          .buttonStyle(.borderedProminent)
      }
    case .loaded(let snapshot):
      quoteHero(snapshot)
      if let streamWarning {
        QuantXStatusBanner(
          title: "实时流暂时中断",
          message: streamWarning,
          status: .attention
        )
      }
      periodPicker
      candleChart(snapshot)
      marketStats(snapshot)
      depthCard
      tradingEntry
      DataFreshnessView(updatedAt: liveQuote?.time ?? snapshot.instrument.quote?.time)
    }
  }

  private var displayName: String {
    if case .loaded(let snapshot) = state {
      return snapshot.instrument.displayName
    }
    return initialDisplayName
  }

  private func quoteHero(_ snapshot: MarketInstrumentSnapshot) -> some View {
    let price = liveQuote?.currentPrice ?? snapshot.instrument.quote?.lastPrice
    let change = liveQuote?.change ?? snapshot.instrument.quote?.change
    let percent = liveQuote?.changePercent ?? snapshot.instrument.quote?.changePercent
    return QuantXCard {
      VStack(alignment: .leading, spacing: 14) {
        HStack(alignment: .top) {
          VStack(alignment: .leading, spacing: 4) {
            Text(snapshot.instrument.displayName)
              .font(.title3.bold())
            Text("\(snapshot.instrument.stockCode) · \(marketName(snapshot.instrument))")
              .font(.caption)
              .foregroundStyle(QuantXTheme.secondaryText)
          }
          Spacer()
          StatusBadge(
            title: snapshot.instrument.isTrading == false ? "不可交易" : "可交易",
            systemImage: snapshot.instrument.isTrading == false
              ? "pause.circle.fill" : "checkmark.circle.fill",
            color: snapshot.instrument.isTrading == false
              ? QuantXTheme.warning : QuantXTheme.online
          )
        }

        HStack(alignment: .firstTextBaseline, spacing: 10) {
          Text(PortfolioFormatters.decimal(price))
            .font(.system(.largeTitle, design: .rounded, weight: .bold))
            .monospacedDigit()
          Text("\(signedDecimal(change))  \(PortfolioFormatters.signedPercentage(percent))")
            .font(.headline)
            .monospacedDigit()
        }
        .foregroundStyle(QuantXTheme.trendColor(change))
        .accessibilityElement(children: .combine)
      }
    }
  }

  private var periodPicker: some View {
    Picker("K线周期", selection: $period) {
      ForEach(MarketPeriod.allCases) { period in
        Text(period.title).tag(period)
      }
    }
    .pickerStyle(.segmented)
  }

  private func candleChart(_ snapshot: MarketInstrumentSnapshot) -> some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: 12) {
        SectionTitle(title: "K 线", subtitle: "\(period.title) · 最近 \(snapshot.candles.count) 根")
        if snapshot.candles.isEmpty {
          ContentUnavailableView {
            Label("暂无 K 线", systemImage: "chart.xyaxis.line")
          } description: {
            Text("服务端在所选周期没有返回数据。")
          }
          .frame(minHeight: 180)
        } else {
          Chart(snapshot.candles) { candle in
            RuleMark(
              x: .value("时间", candle.time),
              yStart: .value("最低", candle.low),
              yEnd: .value("最高", candle.high)
            )
            .foregroundStyle(candle.close >= candle.open ? QuantXTheme.positive : QuantXTheme.negative)

            RectangleMark(
              x: .value("时间", candle.time),
              yStart: .value("开盘", min(candle.open, candle.close)),
              yEnd: .value("收盘", max(candle.open, candle.close)),
              width: .fixed(3)
            )
            .foregroundStyle(candle.close >= candle.open ? QuantXTheme.positive : QuantXTheme.negative)
          }
          .chartYAxis {
            AxisMarks(position: .trailing)
          }
          .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: 4))
          }
          .frame(height: 240)
          .accessibilityLabel("\(snapshot.instrument.displayName) \(period.title) K线图，共 \(snapshot.candles.count) 根")
        }
      }
    }
  }

  private func marketStats(_ snapshot: MarketInstrumentSnapshot) -> some View {
    let quote = snapshot.instrument.quote
    return LazyVGrid(
      columns: [GridItem(.flexible()), GridItem(.flexible())],
      spacing: QuantXTheme.Spacing.medium
    ) {
      QuantXMetricTile(
        title: "今开",
        value: PortfolioFormatters.decimal(liveQuote?.open ?? quote?.open)
      )
      QuantXMetricTile(
        title: "最高",
        value: PortfolioFormatters.decimal(liveQuote?.high ?? quote?.high)
      )
      QuantXMetricTile(
        title: "最低",
        value: PortfolioFormatters.decimal(liveQuote?.low ?? quote?.low)
      )
      QuantXMetricTile(
        title: "昨收",
        value: PortfolioFormatters.decimal(liveQuote?.previousClose ?? quote?.preClose)
      )
      QuantXMetricTile(
        title: "成交额",
        value: compactAmount(liveQuote?.amount ?? quote?.amount)
      )
      QuantXMetricTile(
        title: "换手率",
        value: PortfolioFormatters.percentage(quote?.turnoverRate)
      )
    }
  }

  private var depthCard: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(title: "五档盘口", subtitle: "WebSocket 实时深度") {
          if let depth {
            Text(depth.time.formatted(date: .omitted, time: .standard))
              .font(.caption)
              .foregroundStyle(QuantXTheme.secondaryText)
              .monospacedDigit()
          }
        }
        if let depth {
          ForEach(Array(depth.asks.prefix(5).enumerated()).reversed(), id: \.offset) { index, level in
            DepthLevelRow(
              title: "卖\(index + 1)",
              level: level,
              color: QuantXTheme.negative
            )
          }
          Divider()
          ForEach(Array(depth.bids.prefix(5).enumerated()), id: \.offset) { index, level in
            DepthLevelRow(
              title: "买\(index + 1)",
              level: level,
              color: QuantXTheme.positive
            )
          }
        } else {
          HStack(spacing: 10) {
            ProgressView()
            Text("等待真实盘口推送…")
              .font(.subheadline)
              .foregroundStyle(QuantXTheme.secondaryText)
          }
          .frame(maxWidth: .infinity, minHeight: 80, alignment: .leading)
        }
      }
    }
  }

  private var tradingEntry: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(title: "交易", subtitle: "进入安全票据后再填写数量与报价")
        HStack(spacing: QuantXTheme.Spacing.medium) {
          Button {
            model.openManualOrder(instrumentCode: stockCode, direction: .buy)
          } label: {
            Label("买入", systemImage: "arrow.down.circle.fill")
              .frame(maxWidth: .infinity)
          }
          .buttonStyle(.borderedProminent)
          .tint(QuantXTheme.positive)

          Button {
            model.openManualOrder(instrumentCode: stockCode, direction: .sell)
          } label: {
            Label("卖出", systemImage: "arrow.up.circle.fill")
              .frame(maxWidth: .infinity)
          }
          .buttonStyle(.borderedProminent)
          .tint(QuantXTheme.negative)
        }
        Text("切换到交易页不会直接提交订单；每笔实盘仍需服务端预览和生物识别确认。")
          .font(.caption)
          .foregroundStyle(QuantXTheme.secondaryText)
      }
    }
  }

  private func load() async {
    state = .loading
    do {
      if let snapshot = try await model.loadMarketInstrument(stockCode: stockCode, period: period) {
        state = .loaded(snapshot)
      } else {
        state = .notFound
      }
    } catch is CancellationError {
      return
    } catch {
      state = .failed((error as? LocalizedError)?.errorDescription ?? "行情暂时不可用")
    }
  }

  private func consumeQuoteUpdates() async {
    do {
      for try await value in try model.marketQuoteUpdates(stockCode: stockCode) {
        liveQuote = value
        streamWarning = nil
      }
    } catch is CancellationError {
      return
    } catch {
      streamWarning = (error as? LocalizedError)?.errorDescription ?? "实时报价连接已中断"
    }
  }

  private func consumeDepthUpdates() async {
    do {
      for try await value in try model.marketDepthUpdates(stockCode: stockCode) {
        depth = value
        streamWarning = nil
      }
    } catch is CancellationError {
      return
    } catch {
      streamWarning = (error as? LocalizedError)?.errorDescription ?? "盘口连接已中断"
    }
  }

  private func marketName(_ instrument: MarketInstrument) -> String {
    instrument.exchangeCode ?? instrument.market ?? "市场未知"
  }

  private func signedDecimal(_ value: Double?) -> String {
    guard let value, value.isFinite else { return "—" }
    return String(format: "%+.2f", value)
  }

  private func compactAmount(_ value: Double?) -> String {
    guard let value, value.isFinite else { return "—" }
    if value >= 100_000_000 {
      return String(format: "%.2f 亿", value / 100_000_000)
    }
    if value >= 10_000 {
      return String(format: "%.2f 万", value / 10_000)
    }
    return PortfolioFormatters.decimal(value)
  }
}

private struct DepthLevelRow: View {
  let title: String
  let level: MarketDepthLevel
  let color: Color

  var body: some View {
    HStack {
      Text(title)
        .font(.caption.weight(.semibold))
        .foregroundStyle(color)
        .frame(width: 34, alignment: .leading)
      Text(PortfolioFormatters.decimal(level.price))
        .font(.subheadline.weight(.medium))
        .foregroundStyle(color)
        .monospacedDigit()
      Spacer()
      Text(PortfolioFormatters.integer(level.volume))
        .font(.subheadline)
        .monospacedDigit()
    }
    .accessibilityElement(children: .combine)
  }
}
