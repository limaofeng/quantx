import SwiftUI

struct DashboardView: View {
  private enum Route: Hashable {
    case serviceStatus
    case position(PortfolioPosition)
    case tTradeAssistant
    case limitUpBoardAssistant
    case settings
  }

  @EnvironmentObject private var model: AppModel
  @State private var path: [Route] = []

  private let overviewColumns = [
    GridItem(.adaptive(minimum: 156), spacing: 12, alignment: .top)
  ]

  var body: some View {
    NavigationStack(path: $path) {
      ScrollView {
        LazyVStack(alignment: .leading, spacing: 16) {
          dashboardHeader

          if !model.accountDataEnabled {
            connectionBanner
          }

          accountContent
          monitoringOverview
          tradingAssistantsOverview
          positionContent
        }
        .padding(.horizontal, 16)
        .padding(.top, 8)
        .padding(.bottom, 24)
      }
      .background(QuantXTheme.canvasBackground)
      .navigationTitle("今日")
      .navigationBarTitleDisplayMode(.inline)
      .navigationDestination(for: Route.self) { route in
        switch route {
        case .serviceStatus:
          ServiceStatusView()
        case .position(let position):
          PortfolioPositionDetailView(position: position)
        case .tTradeAssistant:
          TTradeAssistantView()
        case .limitUpBoardAssistant:
          LimitUpBoardAssistantView()
        case .settings:
          SettingsView(embeddedInNavigation: true)
        }
      }
      .toolbar {
        ToolbarItem(placement: .topBarLeading) {
          Button {
            path.append(.settings)
          } label: {
            Image(systemName: "person.crop.circle")
          }
          .frame(minWidth: 44, minHeight: 44)
          .accessibilityLabel("账户与设置")
        }
        ToolbarItem(placement: .topBarTrailing) {
          Button {
            Task { await refreshDashboard() }
          } label: {
            if isRefreshing {
              ProgressView()
            } else {
              Image(systemName: "arrow.clockwise")
            }
          }
          .frame(minWidth: 44, minHeight: 44)
          .accessibilityLabel(isRefreshing ? "正在刷新今日" : "刷新今日")
          .disabled(isRefreshing)
        }
      }
      .refreshable {
        await refreshDashboard()
      }
    }
  }

  private var dashboardHeader: some View {
    HStack(alignment: .top, spacing: 12) {
      VStack(alignment: .leading, spacing: 5) {
        Text("今日概览")
          .font(.title2.bold())
        Text(Date.now.formatted(.dateTime.month().day().weekday(.wide)))
          .font(.subheadline)
          .foregroundStyle(QuantXTheme.secondaryText)
      }

      Spacer(minLength: 8)
      NavigationLink(value: Route.serviceStatus) {
        serviceBadge
      }
      .buttonStyle(.plain)
      .accessibilityHint("查看服务连接详情")
    }
  }

  @ViewBuilder
  private var serviceBadge: some View {
    switch model.serviceState {
    case .idle, .loading:
      StatusBadge(title: "连接中", systemImage: "circle.dotted", color: QuantXTheme.secondaryText)
    case .loaded(let snapshot):
      StatusBadge(
        title: snapshot.isReady ? "服务正常" : "服务异常",
        systemImage: snapshot.isReady ? "checkmark.circle.fill" : "exclamationmark.circle.fill",
        color: snapshot.isReady ? QuantXTheme.online : QuantXTheme.warning
      )
    case .failed:
      StatusBadge(title: "连接异常", systemImage: "wifi.exclamationmark", color: QuantXTheme.warning)
    }
  }

  private var connectionBanner: some View {
    NavigationLink(value: Route.serviceStatus) {
      HStack(spacing: 12) {
        Image(systemName: "lock.shield.fill")
          .font(.body.weight(.semibold))
          .foregroundStyle(QuantXTheme.warning)
          .frame(width: 32, height: 32)
          .background(QuantXTheme.warning.opacity(0.12), in: Circle())
          .accessibilityHidden(true)

        VStack(alignment: .leading, spacing: 3) {
          Text("开发环境 · 账户数据待连接")
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(.primary)
          Text("当前仅检查服务状态，资产与交易数据尚未接入")
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
            .fixedSize(horizontal: false, vertical: true)
        }

        Spacer(minLength: 6)
        Image(systemName: "chevron.right")
          .font(.caption.weight(.semibold))
          .foregroundStyle(.tertiary)
          .accessibilityHidden(true)
      }
      .padding(12)
      .background(QuantXTheme.warning.opacity(0.08), in: RoundedRectangle(cornerRadius: 16))
      .overlay {
        RoundedRectangle(cornerRadius: 16)
          .stroke(QuantXTheme.warning.opacity(0.18), lineWidth: 1)
      }
      .accessibilityElement(children: .combine)
    }
    .buttonStyle(.plain)
    .frame(minHeight: 56)
    .accessibilityHint("查看连接与服务详情")
  }

  @ViewBuilder
  private var accountContent: some View {
    switch model.portfolioState {
    case .unavailable:
      LockedAccountOverviewCard()
    case .idle, .loading:
      AccountLoadingCard()
    case .noAccount(let fetchedAt):
      AccountEmptyCard(
        title: "当前没有授权账户",
        detail: "检查于 \(fetchedAt.formatted(date: .omitted, time: .standard))",
        systemImage: "person.crop.circle.badge.questionmark"
      )
    case .failed(let message):
      AccountEmptyCard(
        title: "账户概览暂不可用",
        detail: message,
        systemImage: "wifi.exclamationmark",
        retry: { Task { await model.refreshPortfolio() } }
      )
    case .loaded(let snapshot, let refreshWarning):
      if let refreshWarning {
        RefreshWarningView(message: refreshWarning)
      }
      AccountHeroCard(snapshot: snapshot)
      AssetAllocationCard(snapshot: snapshot)
    }
  }

  private var monitoringOverview: some View {
    VStack(alignment: .leading, spacing: 10) {
      SectionTitle(title: "运行监控", subtitle: "策略与今日交易动态")

      LazyVGrid(columns: overviewColumns, spacing: 12) {
        DashboardFeatureCard(
          title: "策略监控",
          systemImage: "waveform.path.ecg.rectangle.fill",
          color: QuantXTheme.accent,
          primaryValue: strategyPrimaryValue,
          detail: strategyDetail,
          status: strategyStatus
        ) {
          model.selectedTab = .quant
        }

        DashboardFeatureCard(
          title: "今日动态",
          systemImage: "doc.text.magnifyingglass",
          color: QuantXTheme.warning,
          primaryValue: tradingPrimaryValue,
          detail: tradingDetail,
          status: tradingStatus
        ) {
          model.selectedTab = .trade
        }
      }
    }
  }

  private var tradingAssistantsOverview: some View {
    VStack(alignment: .leading, spacing: 10) {
      SectionTitle(title: "交易助手", subtitle: "服务端策略投影与统一执行状态")

      LazyVGrid(columns: overviewColumns, spacing: 12) {
        DashboardFeatureCard(
          title: "做T助手",
          systemImage: "arrow.triangle.2.circlepath.circle.fill",
          color: QuantXTheme.accent,
          primaryValue: tTradePrimaryValue,
          detail: tTradeDetail,
          status: tTradeStatus
        ) {
          path.append(.tTradeAssistant)
        }

        DashboardFeatureCard(
          title: "打板助手",
          systemImage: "scope",
          color: QuantXTheme.positive,
          primaryValue: limitUpPrimaryValue,
          detail: limitUpDetail,
          status: limitUpStatus
        ) {
          path.append(.limitUpBoardAssistant)
        }
      }
    }
  }

  @ViewBuilder
  private var positionContent: some View {
    switch model.portfolioState {
    case .loaded(let snapshot, _):
      positionsSection(snapshot.positions)
    case .unavailable:
      VStack(alignment: .leading, spacing: 10) {
        SectionTitle(title: "主要持仓", subtitle: "按市值展示前三项")
        QuantXCard {
          HStack(spacing: 14) {
            Image(systemName: "chart.bar.doc.horizontal")
              .font(.title2)
              .foregroundStyle(QuantXTheme.accent)
              .frame(width: 42, height: 42)
              .background(QuantXTheme.accent.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
              .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
              Text("持仓数据待连接")
                .font(.subheadline.weight(.semibold))
              Text("连接个人量化账户后显示持仓市值、数量与盈亏")
                .font(.caption)
                .foregroundStyle(QuantXTheme.secondaryText)
            }
            Spacer(minLength: 6)
          }
          .accessibilityElement(children: .combine)
        }
      }
    default:
      EmptyView()
    }
  }

  private func positionsSection(_ positions: [PortfolioPosition]) -> some View {
    VStack(alignment: .leading, spacing: 10) {
      SectionTitle(title: "主要持仓", subtitle: "按当前组合顺序展示") {
        Button("查看全部") {
          model.selectedTab = .assets
        }
        .font(.subheadline.weight(.semibold))
        .frame(minHeight: 44)
        .accessibilityHint("切换到资产标签页")
      }

      if positions.isEmpty {
        QuantXCard {
          Label {
            VStack(alignment: .leading, spacing: 4) {
              Text("当前没有持仓")
                .font(.subheadline.weight(.semibold))
              Text("后端返回的账户组合暂时为空")
                .font(.caption)
                .foregroundStyle(QuantXTheme.secondaryText)
            }
          } icon: {
            Image(systemName: "tray")
              .foregroundStyle(QuantXTheme.secondaryText)
          }
        }
      } else {
        ForEach(positions.prefix(3)) { position in
          NavigationLink(value: Route.position(position)) {
            QuantXCard {
              HStack(spacing: 10) {
                PortfolioPositionRow(position: position)
                Image(systemName: "chevron.right")
                  .font(.caption.weight(.semibold))
                  .foregroundStyle(.tertiary)
                  .accessibilityHidden(true)
              }
            }
          }
          .buttonStyle(.plain)
          .frame(minHeight: 44)
          .accessibilityHint("查看持仓详情")
        }
      }
    }
  }

  private var strategyPrimaryValue: String {
    guard let snapshot = model.strategyState.snapshot else {
      return model.strategyState.failureMessage == nil ? "待连接" : "读取失败"
    }
    return "\(snapshot.instances.filter { $0.status == "RUNNING" }.count) 运行中"
  }

  private var strategyDetail: String {
    guard let snapshot = model.strategyState.snapshot else {
      return stateDetail(for: model.strategyState)
    }
    let errorCount = snapshot.instances.filter { $0.status == "ERROR" }.count
    return errorCount > 0
      ? "\(snapshot.instances.count) 个实例 · \(errorCount) 个异常"
      : "\(snapshot.instances.count) 个实例 · 无异常"
  }

  private var strategyStatus: DashboardFeatureStatus {
    guard let snapshot = model.strategyState.snapshot else {
      return stateStatus(for: model.strategyState)
    }
    return snapshot.instances.contains { $0.status == "ERROR" } ? .warning : .ready
  }

  private var tradingPrimaryValue: String {
    guard let snapshot = model.tradingState.snapshot else {
      return model.tradingState.failureMessage == nil ? "待连接" : "读取失败"
    }
    return "\(snapshot.todayOrders.count) 笔委托"
  }

  private var tradingDetail: String {
    guard let snapshot = model.tradingState.snapshot else {
      return stateDetail(for: model.tradingState)
    }
    return "\(snapshot.todayTrades.count) 笔成交 · 今日"
  }

  private var tradingStatus: DashboardFeatureStatus {
    guard model.tradingState.snapshot != nil else {
      return stateStatus(for: model.tradingState)
    }
    return .ready
  }

  private var tTradePrimaryValue: String {
    switch model.tTradeAssistantState {
    case .loaded(let snapshot, _): "\(snapshot.activeBatchCount) 个活跃批次"
    case .failed: "读取失败"
    case .noAccount: "没有账户"
    case .unavailable: "未授权"
    case .idle: "待连接"
    case .loading: "连接中"
    }
  }

  private var tTradeDetail: String {
    switch model.tTradeAssistantState {
    case .loaded(let snapshot, _):
      "\(snapshot.pendingSignalCount) 个待确认 · \(snapshot.eligibleCount) 只可监控"
    case .failed(let message), .unavailable(let message): message
    case .noAccount: "当前会话没有授权账户"
    case .idle: "等待读取"
    case .loading: "正在读取账户级投影"
    }
  }

  private var tTradeStatus: DashboardFeatureStatus {
    switch model.tTradeAssistantState {
    case .loaded(let snapshot, _):
      snapshot.killSwitch || snapshot.lastError != nil ? .warning : .ready
    case .failed: .warning
    case .loading: .loading
    case .unavailable, .idle, .noAccount: .locked
    }
  }

  private var limitUpPrimaryValue: String {
    switch model.limitUpBoardState {
    case .loaded(let snapshot, _): "\(snapshot.approvals.count) 个待确认"
    case .failed: "读取失败"
    case .noStrategy: "未配置实例"
    case .unavailable: "未授权"
    case .idle: "待连接"
    case .loading: "连接中"
    }
  }

  private var limitUpDetail: String {
    switch model.limitUpBoardState {
    case .loaded(let snapshot, _): "\(snapshot.exitPlans.count) 个活跃退出计划"
    case .failed(let message), .unavailable(let message): message
    case .noStrategy: "需先创建单标的打板策略"
    case .idle: "等待读取"
    case .loading: "正在读取信号与退出计划"
    }
  }

  private var limitUpStatus: DashboardFeatureStatus {
    switch model.limitUpBoardState {
    case .loaded: .ready
    case .failed: .warning
    case .loading: .loading
    case .unavailable, .idle, .noStrategy: .locked
    }
  }

  private var isRefreshing: Bool {
    model.portfolioRefreshInProgress
      || model.strategyRefreshInProgress
      || model.tradingRefreshInProgress
      || model.tTradeAssistantRefreshInProgress
      || model.limitUpBoardRefreshInProgress
      || model.marketRefreshInProgress
  }

  private func stateDetail(for state: StrategyMonitorState) -> String {
    switch state {
    case .unavailable: "连接后显示"
    case .idle: "等待读取"
    case .loading: "正在读取"
    case .loaded: "已同步"
    case .failed(let message): message
    }
  }

  private func stateDetail(for state: TradingActivityState) -> String {
    switch state {
    case .unavailable: "连接后显示"
    case .idle: "等待读取"
    case .loading: "正在读取"
    case .noAccount: "没有授权账户"
    case .loaded: "已同步"
    case .failed(let message): message
    }
  }

  private func stateStatus(for state: StrategyMonitorState) -> DashboardFeatureStatus {
    switch state {
    case .loading: .loading
    case .failed: .warning
    case .loaded: .ready
    case .unavailable, .idle: .locked
    }
  }

  private func stateStatus(for state: TradingActivityState) -> DashboardFeatureStatus {
    switch state {
    case .loading: .loading
    case .failed: .warning
    case .loaded: .ready
    case .unavailable, .idle, .noAccount: .locked
    }
  }

  private func refreshDashboard() async {
    await model.refreshHealth()
    await model.refreshPortfolio()
    await model.refreshMarket()
    await model.refreshStrategies()
    await model.refreshTradingActivity()
    await model.refreshTTradeAssistant()
    await model.refreshLimitUpBoard()
  }
}

private struct AccountHeroCard: View {
  let snapshot: PortfolioSnapshot

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      HStack(alignment: .top, spacing: 12) {
        VStack(alignment: .leading, spacing: 4) {
          Text(snapshot.account.name)
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(.white)
          Text("总资产")
            .font(.caption)
            .foregroundStyle(.white)
        }
        Spacer(minLength: 8)
        DataFreshnessView(updatedAt: snapshot.sourceUpdatedAt)
      }

      Text(PortfolioFormatters.currency(snapshot.metrics.totalAsset))
        .font(.system(.title, design: .rounded, weight: .bold))
        .monospacedDigit()
        .foregroundStyle(.white)
        .contentTransition(.numericText())
        .minimumScaleFactor(0.72)
        .accessibilityLabel("总资产 \(PortfolioFormatters.currency(snapshot.metrics.totalAsset))")

      HStack(alignment: .top, spacing: 18) {
        HeroMetric(
          title: "当日盈亏",
          value: PortfolioFormatters.currency(snapshot.metrics.todayProfitLoss),
          detail: PortfolioFormatters.signedPercentage(snapshot.metrics.todayProfitLossPercent),
          trend: snapshot.metrics.todayProfitLoss
        )
        HeroMetric(
          title: "累计盈亏",
          value: PortfolioFormatters.currency(snapshot.metrics.totalProfitLoss),
          detail: PortfolioFormatters.signedPercentage(snapshot.metrics.totalProfitLossPercent),
          trend: snapshot.metrics.totalProfitLoss
        )
      }
    }
    .padding(18)
    .background(
      LinearGradient(
        colors: [Color(red: 0.08, green: 0.18, blue: 0.36), Color(red: 0.05, green: 0.10, blue: 0.22)],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
      ),
      in: RoundedRectangle(cornerRadius: 22)
    )
    .overlay(alignment: .topTrailing) {
      Circle()
        .fill(.white.opacity(0.035))
        .frame(width: 180, height: 180)
        .offset(x: 52, y: -74)
        .accessibilityHidden(true)
    }
    .clipped()
  }
}

private struct LockedAccountOverviewCard: View {
  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      HStack(alignment: .top) {
        VStack(alignment: .leading, spacing: 4) {
          Text("账户总览")
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(.white)
          Text("总资产")
            .font(.caption)
            .foregroundStyle(.white)
        }
        Spacer()
        Label("待连接", systemImage: "lock.fill")
          .font(.caption.weight(.semibold))
          .foregroundStyle(.white)
          .padding(.horizontal, 9)
          .padding(.vertical, 5)
          .background(.white.opacity(0.10), in: Capsule())
      }

      Text("连接后显示")
        .font(.title2.bold())
        .foregroundStyle(.white)

      HStack(alignment: .top, spacing: 18) {
        LockedHeroMetric(title: "当日盈亏")
        LockedHeroMetric(title: "累计盈亏")
      }
    }
    .padding(18)
    .background(
      LinearGradient(
        colors: [Color(red: 0.08, green: 0.18, blue: 0.36), Color(red: 0.05, green: 0.10, blue: 0.22)],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
      ),
      in: RoundedRectangle(cornerRadius: 22)
    )
    .overlay(alignment: .topTrailing) {
      Circle()
        .fill(.white.opacity(0.035))
        .frame(width: 180, height: 180)
        .offset(x: 52, y: -74)
        .accessibilityHidden(true)
    }
    .clipped()
  }
}

private struct HeroMetric: View {
  let title: String
  let value: String
  let detail: String
  let trend: Double?

  var body: some View {
    VStack(alignment: .leading, spacing: 4) {
      Text(title)
        .font(.caption)
        .foregroundStyle(.white)
      Text(value)
        .font(.subheadline.weight(.semibold))
        .monospacedDigit()
        .foregroundStyle(trendColor)
      Text(detail)
        .font(.caption)
        .monospacedDigit()
        .foregroundStyle(trendColor.opacity(0.9))
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .accessibilityElement(children: .combine)
  }

  private var trendColor: Color {
    guard let trend else { return .white }
    if trend > 0 { return Color(red: 1.0, green: 0.55, blue: 0.55) }
    if trend < 0 { return Color(red: 0.45, green: 0.92, blue: 0.65) }
    return .white
  }
}

private struct LockedHeroMetric: View {
  let title: String

  var body: some View {
    VStack(alignment: .leading, spacing: 4) {
      Text(title)
        .font(.caption)
        .foregroundStyle(.white)
      Text("待连接")
        .font(.subheadline.weight(.semibold))
        .foregroundStyle(.white)
      Text("暂无快照")
        .font(.caption)
        .foregroundStyle(.white)
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .accessibilityElement(children: .combine)
  }
}

private struct AccountLoadingCard: View {
  var body: some View {
    QuantXCard {
      HStack(spacing: 12) {
        ProgressView()
        VStack(alignment: .leading, spacing: 4) {
          Text("正在读取账户概览")
            .font(.subheadline.weight(.semibold))
          Text("资产、盈亏和持仓将在同一快照中显示")
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        }
      }
      .frame(minHeight: 100)
      .accessibilityElement(children: .combine)
    }
  }
}

private struct AccountEmptyCard: View {
  let title: String
  let detail: String
  let systemImage: String
  var retry: (() -> Void)?

  var body: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: 12) {
        Label(title, systemImage: systemImage)
          .font(.headline)
        Text(detail)
          .font(.subheadline)
          .foregroundStyle(QuantXTheme.secondaryText)
        if let retry {
          Button("重新加载", action: retry)
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .tint(Color(red: 0.04, green: 0.25, blue: 0.62))
        }
      }
    }
  }
}

private struct AssetAllocationCard: View {
  let snapshot: PortfolioSnapshot

  private var assetBase: Double {
    max(snapshot.metrics.cash + snapshot.metrics.marketValue, 0)
  }

  private var cashRatio: Double {
    guard assetBase > 0 else { return 0 }
    return max(0, min(1, snapshot.metrics.cash / assetBase))
  }

  private var marketRatio: Double {
    guard assetBase > 0 else { return 0 }
    return max(0, min(1, snapshot.metrics.marketValue / assetBase))
  }

  var body: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: 14) {
        SectionTitle(title: "资产构成", subtitle: "现金与持仓市值")

        GeometryReader { proxy in
          HStack(spacing: 3) {
            RoundedRectangle(cornerRadius: 4)
              .fill(QuantXTheme.accent)
              .frame(width: max(0, proxy.size.width * cashRatio - 1.5))
            RoundedRectangle(cornerRadius: 4)
              .fill(QuantXTheme.positive)
              .frame(width: max(0, proxy.size.width * marketRatio - 1.5))
          }
        }
        .frame(height: 9)
        .background(Color.primary.opacity(0.08), in: Capsule())
        .accessibilityHidden(true)

        HStack(spacing: 16) {
          AllocationLegend(
            title: "可用资金",
            value: PortfolioFormatters.currency(snapshot.metrics.cash),
            ratio: cashRatio,
            color: QuantXTheme.accent
          )
          AllocationLegend(
            title: "持仓市值",
            value: PortfolioFormatters.currency(snapshot.metrics.marketValue),
            ratio: marketRatio,
            color: QuantXTheme.positive
          )
        }
      }
    }
  }
}

private struct AllocationLegend: View {
  let title: String
  let value: String
  let ratio: Double
  let color: Color

  var body: some View {
    HStack(alignment: .top, spacing: 8) {
      Circle()
        .fill(color)
        .frame(width: 8, height: 8)
        .padding(.top, 5)
        .accessibilityHidden(true)
      VStack(alignment: .leading, spacing: 3) {
        Text(title)
          .font(.caption)
          .foregroundStyle(QuantXTheme.secondaryText)
        Text(value)
          .font(.subheadline.weight(.semibold))
          .monospacedDigit()
        Text(ratio.formatted(.percent.precision(.fractionLength(1))))
          .font(.caption2)
          .monospacedDigit()
          .foregroundStyle(QuantXTheme.secondaryText)
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .accessibilityElement(children: .combine)
  }
}

private enum DashboardFeatureStatus {
  case locked
  case loading
  case ready
  case warning
}

private struct DashboardFeatureCard: View {
  let title: String
  let systemImage: String
  let color: Color
  let primaryValue: String
  let detail: String
  let status: DashboardFeatureStatus
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      VStack(alignment: .leading, spacing: 14) {
        HStack {
          Image(systemName: systemImage)
            .font(.body.weight(.semibold))
            .foregroundStyle(color)
            .frame(width: 36, height: 36)
            .background(color.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
            .accessibilityHidden(true)
          Spacer()
          statusIcon
        }

        VStack(alignment: .leading, spacing: 4) {
          Text(title)
            .font(.caption.weight(.semibold))
            .foregroundStyle(QuantXTheme.secondaryText)
            .fixedSize(horizontal: false, vertical: true)
          Text(primaryValue)
            .font(.title3.bold())
            .monospacedDigit()
            .foregroundStyle(.primary)
            .fixedSize(horizontal: false, vertical: true)
          Text(detail)
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
            .fixedSize(horizontal: false, vertical: true)
        }
      }
      .frame(maxWidth: .infinity, minHeight: 126, alignment: .leading)
      .padding(14)
      .background(QuantXTheme.cardBackground, in: RoundedRectangle(cornerRadius: 18))
      .contentShape(RoundedRectangle(cornerRadius: 18))
      .accessibilityElement(children: .combine)
    }
    .buttonStyle(.plain)
    .accessibilityHint("打开\(title)")
  }

  @ViewBuilder
  private var statusIcon: some View {
    switch status {
    case .locked:
      Image(systemName: "lock.fill")
        .foregroundStyle(QuantXTheme.secondaryText)
        .accessibilityLabel("待连接")
    case .loading:
      ProgressView()
        .accessibilityLabel("正在读取")
    case .ready:
      Image(systemName: "checkmark.circle.fill")
        .foregroundStyle(QuantXTheme.online)
        .accessibilityLabel("已同步")
    case .warning:
      Image(systemName: "exclamationmark.triangle.fill")
        .foregroundStyle(QuantXTheme.warning)
        .accessibilityLabel("需要关注")
    }
  }
}

struct SectionTitle<Trailing: View>: View {
  let title: String
  let subtitle: String?
  @ViewBuilder let trailing: Trailing

  init(
    title: String,
    subtitle: String? = nil,
    @ViewBuilder trailing: () -> Trailing
  ) {
    self.title = title
    self.subtitle = subtitle
    self.trailing = trailing()
  }

  var body: some View {
    HStack(alignment: .firstTextBaseline, spacing: 10) {
      VStack(alignment: .leading, spacing: 3) {
        Text(title)
          .font(.headline)
        if let subtitle {
          Text(subtitle)
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        }
      }
      Spacer(minLength: 8)
      trailing
    }
  }
}

extension SectionTitle where Trailing == EmptyView {
  init(title: String, subtitle: String? = nil) {
    self.init(title: title, subtitle: subtitle) { EmptyView() }
  }
}
