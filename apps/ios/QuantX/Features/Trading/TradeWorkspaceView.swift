import SwiftUI

struct TradeWorkspaceView: View {
  private enum Route: Hashable {
    case orderTicket(side: String)
    case activity
    case liquidation
  }

  @EnvironmentObject private var model: AppModel

  var body: some View {
    NavigationStack {
      ScrollView {
        LazyVStack(alignment: .leading, spacing: QuantXTheme.Spacing.large) {
          QuantXStatusBanner(
            title: "统一交易安全链路",
            message: "预览只展示服务端校验结果；确认排队不等于券商受理或成交。",
            status: .attention
          )

          orderEntry
          activitySummary
          liquidationEntry

          Text("所有成交事实仅来自 QMT Agent 回报经 Engine 持久化与收敛后的状态。")
            .font(.footnote)
            .foregroundStyle(QuantXTheme.secondaryText)
            .fixedSize(horizontal: false, vertical: true)
        }
        .padding(QuantXTheme.Spacing.large)
      }
      .background(QuantXTheme.canvasBackground)
      .navigationTitle("交易")
      .navigationDestination(for: Route.self) { route in
        switch route {
        case .orderTicket(let side):
          ManualOrderTicketUnavailableView(side: side)
        case .activity:
          TradingActivityView(embeddedInNavigation: true)
        case .liquidation:
          SafeLiquidationUnavailableView()
        }
      }
      .refreshable {
        await model.refreshTradingActivity()
      }
    }
    .task {
      if case .idle = model.tradingState {
        await model.refreshTradingActivity()
      }
    }
  }

  private var orderEntry: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.large) {
        SectionTitle(title: "手动交易", subtitle: "限价 / 合法最优价")

        HStack(spacing: QuantXTheme.Spacing.medium) {
          NavigationLink(value: Route.orderTicket(side: "BUY")) {
            Label("买入", systemImage: "arrow.down.circle.fill")
              .font(.headline)
              .frame(maxWidth: .infinity, minHeight: 48)
          }
          .buttonStyle(.borderedProminent)
          .tint(QuantXTheme.positive)
          .accessibilityHint("进入买入票据，不会立即提交订单")

          NavigationLink(value: Route.orderTicket(side: "SELL")) {
            Label("卖出", systemImage: "arrow.up.circle.fill")
              .font(.headline)
              .frame(maxWidth: .infinity, minHeight: 48)
          }
          .buttonStyle(.borderedProminent)
          .tint(QuantXTheme.negative)
          .accessibilityHint("进入卖出票据，不会立即提交订单")
        }

        Label(
          "实盘提交必须通过独立 trade:manual 权限、短时预览凭据和 Face ID / Touch ID。",
          systemImage: "faceid"
        )
        .font(.caption)
        .foregroundStyle(QuantXTheme.secondaryText)
      }
    }
  }

  private var activitySummary: some View {
    NavigationLink(value: Route.activity) {
      QuantXCard {
        HStack(alignment: .top, spacing: QuantXTheme.Spacing.medium) {
          Image(systemName: "list.bullet.rectangle.portrait.fill")
            .font(.title2)
            .foregroundStyle(QuantXTheme.accent)
            .frame(width: 42, height: 42)
            .background(QuantXTheme.accent.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
            .accessibilityHidden(true)
          VStack(alignment: .leading, spacing: 5) {
            Text("委托与成交")
              .font(.headline)
              .foregroundStyle(.primary)
            Text(activityDetail)
              .font(.subheadline)
              .foregroundStyle(QuantXTheme.secondaryText)
          }
          Spacer()
          Image(systemName: "chevron.right")
            .font(.caption.weight(.bold))
            .foregroundStyle(.tertiary)
            .accessibilityHidden(true)
        }
        .accessibilityElement(children: .combine)
      }
    }
    .buttonStyle(.plain)
    .accessibilityHint("查看委托、成交及券商终态")
  }

  private var liquidationEntry: some View {
    NavigationLink(value: Route.liquidation) {
      QuantXCard {
        HStack(alignment: .top, spacing: QuantXTheme.Spacing.medium) {
          Image(systemName: "shield.lefthalf.filled.badge.checkmark")
            .font(.title2)
            .foregroundStyle(QuantXTheme.warning)
            .frame(width: 42, height: 42)
            .background(QuantXTheme.warning.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
            .accessibilityHidden(true)
          VStack(alignment: .leading, spacing: 5) {
            Text("清仓与退出")
              .font(.headline)
              .foregroundStyle(.primary)
            Text("个股、选中持仓、条件退出与统一进度")
              .font(.subheadline)
              .foregroundStyle(QuantXTheme.secondaryText)
          }
          Spacer()
          Image(systemName: "chevron.right")
            .font(.caption.weight(.bold))
            .foregroundStyle(.tertiary)
            .accessibilityHidden(true)
        }
        .accessibilityElement(children: .combine)
      }
    }
    .buttonStyle(.plain)
    .accessibilityHint("查看清仓安全能力状态")
  }

  private var activityDetail: String {
    guard let snapshot = model.tradingState.snapshot else {
      return switch model.tradingState {
      case .failed: "读取失败，可进入重试"
      case .loading: "正在同步真实状态"
      case .noAccount: "没有授权账户"
      case .unavailable: "当前会话无读取权限"
      case .idle: "等待同步"
      case .loaded: "已同步"
      }
    }
    return "今日 \(snapshot.todayOrders.count) 笔委托 · \(snapshot.todayTrades.count) 笔成交"
  }
}

private struct ManualOrderTicketUnavailableView: View {
  let side: String

  var body: some View {
    ContentUnavailableView {
      Label(
        side == "BUY" ? "买入票据尚未开放" : "卖出票据尚未开放",
        systemImage: "lock.shield.fill"
      )
    } description: {
      Text("服务端两阶段手动交易契约尚未部署。本页不会调用遗留 placeOrder，也不会绕过预览、幂等或生物识别确认。")
    }
    .navigationTitle(side == "BUY" ? "买入" : "卖出")
    .navigationBarTitleDisplayMode(.inline)
  }
}

private struct SafeLiquidationUnavailableView: View {
  var body: some View {
    ContentUnavailableView {
      Label("清仓安全契约待部署", systemImage: "lock.shield.fill")
    } description: {
      Text("现有清仓 Mutation 会直接执行且没有统一短时预览挑战，因此 iOS 不暴露该路径。安全预览与确认接口完成后再启用。")
    }
    .navigationTitle("清仓与退出")
    .navigationBarTitleDisplayMode(.inline)
  }
}
