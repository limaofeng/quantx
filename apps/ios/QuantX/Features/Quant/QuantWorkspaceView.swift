import SwiftUI

struct QuantWorkspaceView: View {
  private enum Route: Hashable {
    case strategies
    case tTrade
    case limitUp
  }

  @EnvironmentObject private var model: AppModel

  var body: some View {
    NavigationStack {
      ScrollView {
        LazyVStack(alignment: .leading, spacing: QuantXTheme.Spacing.large) {
          statusSummary
          SectionTitle(title: "量化能力", subtitle: "策略、做T与打板执行闭环")
          featureCard(
            route: .strategies,
            title: "策略中心",
            detail: strategyDetail,
            systemImage: "waveform.path.ecg.rectangle.fill",
            color: QuantXTheme.accent,
            status: strategyStatus
          )
          featureCard(
            route: .tTrade,
            title: "做T助手",
            detail: tTradeDetail,
            systemImage: "arrow.triangle.2.circlepath.circle.fill",
            color: QuantXTheme.accent,
            status: tTradeStatus
          )
          featureCard(
            route: .limitUp,
            title: "打板助手",
            detail: limitUpDetail,
            systemImage: "scope",
            color: QuantXTheme.positive,
            status: limitUpStatus
          )
        }
        .padding(QuantXTheme.Spacing.large)
      }
      .background(QuantXTheme.canvasBackground)
      .navigationTitle("量化")
      .navigationDestination(for: Route.self) { route in
        switch route {
        case .strategies:
          StrategiesView(embeddedInNavigation: true)
        case .tTrade:
          TTradeAssistantView()
        case .limitUp:
          LimitUpBoardAssistantView()
        }
      }
      .refreshable {
        await refresh()
      }
    }
    .task {
      if case .idle = model.strategyState { await model.refreshStrategies() }
      if case .idle = model.tTradeAssistantState { await model.refreshTTradeAssistant() }
      if case .idle = model.limitUpBoardState { await model.refreshLimitUpBoard() }
    }
  }

  private var statusSummary: some View {
    let hasBlockingIssue = tTradeStatus == .blocked || strategyStatus == .attention
    return QuantXStatusBanner(
      title: hasBlockingIssue ? "量化能力需要关注" : "个人量化控制中心",
      message: "策略只产生交易意图；合法数量、T+1、资金、涨跌停与最终订单由统一交易域处理。",
      status: hasBlockingIssue ? .attention : .ready
    )
  }

  private func featureCard(
    route: Route,
    title: String,
    detail: String,
    systemImage: String,
    color: Color,
    status: QuantXSemanticStatus
  ) -> some View {
    NavigationLink(value: route) {
      QuantXCard {
        HStack(alignment: .top, spacing: QuantXTheme.Spacing.medium) {
          Image(systemName: systemImage)
            .font(.title2)
            .foregroundStyle(color)
            .frame(width: 44, height: 44)
            .background(color.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
            .accessibilityHidden(true)
          VStack(alignment: .leading, spacing: 5) {
            Text(title)
              .font(.headline)
              .foregroundStyle(.primary)
            Text(detail)
              .font(.subheadline)
              .foregroundStyle(QuantXTheme.secondaryText)
              .fixedSize(horizontal: false, vertical: true)
          }
          Spacer(minLength: 8)
          StatusBadge(title: status.title, systemImage: status.systemImage, color: status.color)
        }
        .accessibilityElement(children: .combine)
      }
    }
    .buttonStyle(.plain)
    .accessibilityHint("打开\(title)")
  }

  private var strategyDetail: String {
    guard let snapshot = model.strategyState.snapshot else {
      return stateMessage(model.strategyState)
    }
    let running = snapshot.instances.filter { $0.status == "RUNNING" }.count
    let errors = snapshot.instances.filter { $0.status == "ERROR" }.count
    return "\(running) 个运行中 · \(errors) 个异常 · 共 \(snapshot.instances.count) 个实例"
  }

  private var strategyStatus: QuantXSemanticStatus {
    guard let snapshot = model.strategyState.snapshot else {
      return stateStatus(model.strategyState)
    }
    return snapshot.instances.contains { $0.status == "ERROR" } ? .attention : .ready
  }

  private var tTradeDetail: String {
    switch model.tTradeAssistantState {
    case .loaded(let snapshot, _):
      "\(snapshot.activeBatchCount) 个活跃批次 · \(snapshot.pendingSignalCount) 个待确认信号"
    case .failed(let message), .unavailable(let message): message
    case .noAccount: "没有授权账户"
    case .idle: "等待同步"
    case .loading: "正在同步账户级投影"
    }
  }

  private var tTradeStatus: QuantXSemanticStatus {
    switch model.tTradeAssistantState {
    case .loaded(let snapshot, _):
      snapshot.killSwitch ? .blocked : snapshot.lastError == nil ? .ready : .attention
    case .loading: .working
    case .failed: .attention
    case .unavailable, .idle, .noAccount: .unavailable
    }
  }

  private var limitUpDetail: String {
    switch model.limitUpBoardState {
    case .loaded(let snapshot, _):
      "\(snapshot.approvals.count) 个待确认意图 · \(snapshot.exitPlans.count) 个退出计划"
    case .failed(let message), .unavailable(let message): message
    case .noStrategy: "尚未配置打板策略实例"
    case .idle: "等待同步"
    case .loading: "正在同步信号与退出计划"
    }
  }

  private var limitUpStatus: QuantXSemanticStatus {
    switch model.limitUpBoardState {
    case .loaded: .ready
    case .loading: .working
    case .failed: .attention
    case .unavailable, .idle, .noStrategy: .unavailable
    }
  }

  private func stateMessage(_ state: StrategyMonitorState) -> String {
    switch state {
    case .unavailable(let message), .failed(let message): message
    case .idle: "等待同步"
    case .loading: "正在同步策略状态"
    case .loaded: "已同步"
    }
  }

  private func stateStatus(_ state: StrategyMonitorState) -> QuantXSemanticStatus {
    switch state {
    case .loaded: .ready
    case .loading: .working
    case .failed: .attention
    case .unavailable, .idle: .unavailable
    }
  }

  private func refresh() async {
    await model.refreshStrategies()
    await model.refreshTTradeAssistant()
    await model.refreshLimitUpBoard()
  }
}
