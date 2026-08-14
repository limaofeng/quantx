import SwiftUI

struct TradingActivityView: View {
  private enum TimeScope: String, CaseIterable, Identifiable {
    case today
    case history

    var id: Self { self }
    var title: String { self == .today ? "今日" : "近 30 日" }
  }

  private enum RecordKind: String, CaseIterable, Identifiable {
    case orders
    case trades

    var id: Self { self }
    var title: String { self == .orders ? "委托" : "成交" }
  }

  private enum Route: Hashable {
    case order(OrderRecord)
    case trade(TradeRecord)
  }

  @EnvironmentObject private var model: AppModel
  @State private var timeScope: TimeScope = .today
  @State private var recordKind: RecordKind = .orders
  var embeddedInNavigation = false

  @ViewBuilder
  var body: some View {
    if embeddedInNavigation {
      rootContent
        .task { await loadIfNeeded() }
    } else {
      NavigationStack {
        rootContent
      }
      .task { await loadIfNeeded() }
    }
  }

  private var rootContent: some View {
    content
      .background(QuantXTheme.canvasBackground)
      .navigationTitle("委托成交")
      .navigationDestination(for: Route.self) { route in
        switch route {
        case .order(let order):
          OrderRecordDetailView(order: order)
        case .trade(let trade):
          TradeRecordDetailView(trade: trade)
        }
      }
      .toolbar {
        if model.tradingRefreshInProgress,
          model.tradingState.snapshot != nil
        {
          ToolbarItem(placement: .topBarTrailing) {
            ProgressView()
              .accessibilityLabel("正在刷新委托成交")
          }
        }
      }
  }

  private func loadIfNeeded() async {
    if case .idle = model.tradingState {
      await model.refreshTradingActivity()
    }
  }

  @ViewBuilder
  private var content: some View {
    switch model.tradingState {
    case .unavailable(let reason):
      ContentUnavailableView {
        Label("委托成交查询不可用", systemImage: "lock.shield.fill")
      } description: {
        Text(reason)
      }
    case .idle, .loading:
      ProgressView("正在读取委托与成交快照…")
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityLabel("正在读取委托与成交快照")
    case .noAccount:
      ContentUnavailableView {
        Label("没有可用账户", systemImage: "person.crop.circle.badge.questionmark")
      } description: {
        Text("当前会话没有后端确认的授权账户。")
      }
    case .loaded(let snapshot, let refreshWarning):
      loadedContent(snapshot: snapshot, refreshWarning: refreshWarning)
    case .failed(let message):
      ContentUnavailableView {
        Label("无法读取委托成交", systemImage: "wifi.exclamationmark")
      } description: {
        Text(message)
      } actions: {
        retryButton
      }
    }
  }

  private func loadedContent(
    snapshot: TradingActivitySnapshot,
    refreshWarning: String?
  ) -> some View {
    ScrollView {
      LazyVStack(spacing: 14) {
        if let refreshWarning {
          RefreshWarningView(message: refreshWarning)
        }

        Picker("时间范围", selection: $timeScope) {
          ForEach(TimeScope.allCases) { scope in
            Text(scope.title).tag(scope)
          }
        }
        .pickerStyle(.segmented)

        Picker("记录类型", selection: $recordKind) {
          ForEach(RecordKind.allCases) { kind in
            Text(kind.title).tag(kind)
          }
        }
        .pickerStyle(.segmented)

        summary(snapshot)
        records(snapshot)

        Text("委托状态与成交事实均来自服务端收敛结果；Agent 收到指令不等于券商受理或真实成交。")
          .font(.footnote)
          .foregroundStyle(QuantXTheme.secondaryText)
          .frame(maxWidth: .infinity, alignment: .leading)
          .padding(.vertical, 4)
      }
      .padding(16)
    }
    .refreshable {
      await model.refreshTradingActivity()
    }
  }

  private func summary(_ snapshot: TradingActivitySnapshot) -> some View {
    QuantXCard {
      HStack(alignment: .top) {
        VStack(alignment: .leading, spacing: 5) {
          Text("\(timeScope.title)\(recordKind.title)")
            .font(.headline)
          Text(summaryPeriod(snapshot))
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        }
        Spacer()
        Text("\(recordCount(snapshot)) 条")
          .font(.title3.bold())
          .monospacedDigit()
      }
      .accessibilityElement(children: .combine)
    }
  }

  @ViewBuilder
  private func records(_ snapshot: TradingActivitySnapshot) -> some View {
    if recordCount(snapshot) == 0 {
      QuantXCard {
        ContentUnavailableView {
          Label("暂无\(recordKind.title)", systemImage: "tray")
        } description: {
          Text("后端在所选时间范围内没有返回记录。")
        }
      }
    } else if recordKind == .orders {
      ForEach(selectedOrders(snapshot)) { order in
        NavigationLink(value: Route.order(order)) {
          QuantXCard {
            OrderRecordRow(order: order)
          }
        }
        .buttonStyle(.plain)
        .accessibilityHint("查看委托详情")
      }
    } else {
      ForEach(selectedTrades(snapshot)) { trade in
        NavigationLink(value: Route.trade(trade)) {
          QuantXCard {
            TradeRecordRow(trade: trade)
          }
        }
        .buttonStyle(.plain)
        .accessibilityHint("查看成交详情")
      }
    }
  }

  private func selectedOrders(_ snapshot: TradingActivitySnapshot) -> [OrderRecord] {
    timeScope == .today ? snapshot.todayOrders : snapshot.historyOrders
  }

  private func selectedTrades(_ snapshot: TradingActivitySnapshot) -> [TradeRecord] {
    timeScope == .today ? snapshot.todayTrades : snapshot.historyTrades
  }

  private func recordCount(_ snapshot: TradingActivitySnapshot) -> Int {
    recordKind == .orders ? selectedOrders(snapshot).count : selectedTrades(snapshot).count
  }

  private func summaryPeriod(_ snapshot: TradingActivitySnapshot) -> String {
    if timeScope == .today {
      return "快照获取于 \(snapshot.fetchedAt.formatted(date: .omitted, time: .standard))"
    }
    return "\(snapshot.historyStartDate.formatted(date: .numeric, time: .omitted)) – \(snapshot.historyEndDate.formatted(date: .numeric, time: .omitted))"
  }

  private var retryButton: some View {
    Button("重新加载") {
      Task { await model.refreshTradingActivity() }
    }
    .buttonStyle(.borderedProminent)
    .controlSize(.large)
    .disabled(model.tradingRefreshInProgress)
  }
}

private struct OrderRecordRow: View {
  let order: OrderRecord

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack(alignment: .top, spacing: 10) {
        VStack(alignment: .leading, spacing: 3) {
          Text(order.displayName)
            .font(.headline)
            .foregroundStyle(.primary)
          Text(order.stockCode)
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        }
        Spacer()
        StatusBadge(
          title: order.sideDisplayName,
          systemImage: order.side == "BUY" ? "arrow.down.circle.fill" : "arrow.up.circle.fill",
          color: order.side == "BUY" ? QuantXTheme.positive : QuantXTheme.negative
        )
      }

      HStack {
        VStack(alignment: .leading, spacing: 3) {
          Text(order.statusDisplayName)
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(orderStatusColor)
          Text("委托 \(PortfolioFormatters.integer(order.volume)) · 成交 \(PortfolioFormatters.integer(order.tradedVolume)) · 剩余 \(PortfolioFormatters.integer(order.remainingVolume))")
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        }
        Spacer()
        VStack(alignment: .trailing, spacing: 3) {
          Text(PortfolioFormatters.decimal(order.price))
            .font(.subheadline.weight(.semibold))
            .monospacedDigit()
          Text(order.submittedAt.formatted(date: .omitted, time: .standard))
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
            .monospacedDigit()
        }
      }
    }
    .accessibilityElement(children: .combine)
  }

  private var orderStatusColor: Color {
    switch order.status {
    case "SUCCEEDED": QuantXTheme.online
    case "PART_SUCC", "WAIT_REPORTING", "REPORTED_CANCEL": QuantXTheme.warning
    case "JUNK": QuantXTheme.warning
    case "REPORTED": QuantXTheme.accent
    default: QuantXTheme.secondaryText
    }
  }
}

private struct TradeRecordRow: View {
  let trade: TradeRecord

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack(alignment: .top, spacing: 10) {
        VStack(alignment: .leading, spacing: 3) {
          Text(trade.displayName)
            .font(.headline)
            .foregroundStyle(.primary)
          Text(trade.stockCode)
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        }
        Spacer()
        StatusBadge(
          title: trade.sideDisplayName,
          systemImage: trade.orderType == 23 ? "arrow.down.circle.fill" : "arrow.up.circle.fill",
          color: trade.orderType == 23 ? QuantXTheme.positive : QuantXTheme.negative
        )
      }

      HStack {
        VStack(alignment: .leading, spacing: 3) {
          Text("已成交 \(PortfolioFormatters.integer(trade.volume)) 股")
            .font(.subheadline.weight(.semibold))
          Text("成交额 \(PortfolioFormatters.currency(trade.amount))")
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        }
        Spacer()
        VStack(alignment: .trailing, spacing: 3) {
          Text(PortfolioFormatters.decimal(trade.price))
            .font(.subheadline.weight(.semibold))
            .monospacedDigit()
          Text(trade.executedAt?.formatted(date: .omitted, time: .standard) ?? "时间未知")
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
            .monospacedDigit()
        }
      }
    }
    .accessibilityElement(children: .combine)
  }
}

private struct OrderRecordDetailView: View {
  let order: OrderRecord

  var body: some View {
    List {
      Section("委托") {
        LabeledContent("证券", value: "\(order.displayName) · \(order.stockCode)")
        LabeledContent("方向", value: order.sideDisplayName)
        LabeledContent("状态", value: order.statusDisplayName)
        if let statusMessage = order.statusMessage, !statusMessage.isEmpty {
          LabeledContent("状态说明", value: statusMessage)
        }
        LabeledContent("委托时间") {
          Text(order.submittedAt.formatted(date: .abbreviated, time: .standard))
            .monospacedDigit()
        }
      }

      Section("数量与价格") {
        LabeledContent("委托数量", value: PortfolioFormatters.integer(order.volume))
        LabeledContent("已成交数量", value: PortfolioFormatters.integer(order.tradedVolume))
        LabeledContent("剩余数量", value: PortfolioFormatters.integer(order.remainingVolume))
        LabeledContent("委托价格", value: PortfolioFormatters.decimal(order.price))
        LabeledContent("成交均价", value: PortfolioFormatters.decimal(order.tradedPrice))
      }

      Section("来源") {
        LabeledContent("委托编号", value: order.id)
        LabeledContent("柜台编号", value: order.systemID.isEmpty ? "暂无" : order.systemID)
        LabeledContent("策略", value: order.strategyName ?? "非策略委托")
        if let remark = order.remark, !remark.isEmpty {
          Text(remark)
            .font(.footnote)
        }
      }

      Section("状态说明") {
        Text("“等待报送”“券商已报”“部分成交”和“全部成交”是不同事实；本页不把指令投递或下单成功解释为成交。")
          .font(.footnote)
          .foregroundStyle(QuantXTheme.secondaryText)
      }
    }
    .navigationTitle("委托详情")
    .navigationBarTitleDisplayMode(.inline)
  }
}

private struct TradeRecordDetailView: View {
  let trade: TradeRecord

  var body: some View {
    List {
      Section("成交") {
        LabeledContent("证券", value: "\(trade.displayName) · \(trade.stockCode)")
        LabeledContent("方向", value: trade.sideDisplayName)
        LabeledContent("成交数量", value: PortfolioFormatters.integer(trade.volume))
        LabeledContent("成交价格", value: PortfolioFormatters.decimal(trade.price))
        LabeledContent("成交金额", value: PortfolioFormatters.currency(trade.amount))
        LabeledContent("成交时间") {
          Text(trade.executedAt?.formatted(date: .abbreviated, time: .standard) ?? "未知")
            .monospacedDigit()
        }
      }

      Section("来源") {
        LabeledContent("成交编号", value: trade.id)
        LabeledContent("委托编号", value: String(trade.orderID))
        LabeledContent("柜台编号", value: trade.orderSystemID.isEmpty ? "暂无" : trade.orderSystemID)
        LabeledContent("策略", value: trade.strategyName ?? "非策略成交")
        if let remark = trade.remark, !remark.isEmpty {
          Text(remark)
            .font(.footnote)
        }
      }

      Section("事实来源") {
        Text("成交记录来自 miniQMT 回报经服务端持久化与收敛后的查询结果。")
          .font(.footnote)
          .foregroundStyle(QuantXTheme.secondaryText)
      }
    }
    .navigationTitle("成交详情")
    .navigationBarTitleDisplayMode(.inline)
  }
}
