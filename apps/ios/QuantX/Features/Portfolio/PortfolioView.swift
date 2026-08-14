import SwiftUI

struct PortfolioView: View {
  @EnvironmentObject private var model: AppModel

  var body: some View {
    NavigationStack {
      content
        .background(QuantXTheme.canvasBackground)
        .navigationTitle("持仓")
        .navigationDestination(for: PortfolioPosition.self) { position in
          PortfolioPositionDetailView(position: position)
        }
        .toolbar {
          if model.portfolioRefreshInProgress,
            model.portfolioState.snapshot != nil
          {
            ToolbarItem(placement: .topBarTrailing) {
              ProgressView()
                .accessibilityLabel("正在刷新持仓")
            }
          }
        }
    }
    .task {
      if case .idle = model.portfolioState {
        await model.refreshPortfolio()
      }
    }
  }

  @ViewBuilder
  private var content: some View {
    switch model.portfolioState {
    case .unavailable(let reason):
      ContentUnavailableView {
        Label("账户数据连接已关闭", systemImage: "lock.shield.fill")
      } description: {
        Text(reason)
      }
    case .idle, .loading:
      ProgressView("正在读取账户与持仓…")
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityLabel("正在读取账户与持仓")
    case .noAccount(let fetchedAt):
      ContentUnavailableView {
        Label("没有可用账户", systemImage: "person.crop.circle.badge.questionmark")
      } description: {
        Text("当前会话没有后端确认的授权账户。检查时间：\(fetchedAt.formatted(date: .omitted, time: .standard))")
      } actions: {
        retryButton
      }
    case .loaded(let snapshot, let refreshWarning):
      loadedContent(snapshot: snapshot, refreshWarning: refreshWarning)
    case .failed(let message):
      ContentUnavailableView {
        Label("无法读取持仓", systemImage: "wifi.exclamationmark")
      } description: {
        Text(message)
      } actions: {
        retryButton
      }
    }
  }

  private func loadedContent(
    snapshot: PortfolioSnapshot,
    refreshWarning: String?
  ) -> some View {
    ScrollView {
      LazyVStack(spacing: 14) {
        if let refreshWarning {
          RefreshWarningView(message: refreshWarning)
        }

        PortfolioOverviewCard(snapshot: snapshot)

        HStack(alignment: .firstTextBaseline) {
          Text("全部持仓")
            .font(.headline)
          Spacer()
          Text("\(snapshot.positions.count) 项")
            .font(.subheadline)
            .foregroundStyle(QuantXTheme.secondaryText)
            .monospacedDigit()
        }
        .padding(.top, 4)

        if snapshot.positions.isEmpty {
          QuantXCard {
            ContentUnavailableView {
              Label("暂无持仓", systemImage: "tray")
            } description: {
              Text("后端返回的当前持仓列表为空。")
            }
          }
        } else {
          ForEach(snapshot.positions) { position in
            NavigationLink(value: position) {
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

        Text("本页只展示后端查询结果，不推断成交、可卖量或账户状态。")
          .font(.footnote)
          .foregroundStyle(QuantXTheme.secondaryText)
          .frame(maxWidth: .infinity, alignment: .leading)
          .padding(.vertical, 4)
      }
      .padding(16)
    }
    .refreshable {
      await model.refreshPortfolio()
    }
  }

  private var retryButton: some View {
    Button("重新加载") {
      Task { await model.refreshPortfolio() }
    }
    .buttonStyle(.borderedProminent)
    .controlSize(.large)
    .disabled(model.portfolioRefreshInProgress)
    .accessibilityHint("重新请求后端账户与持仓数据")
  }
}

struct PortfolioPositionDetailView: View {
  let position: PortfolioPosition

  var body: some View {
    List {
      Section {
        LabeledContent("证券代码", value: position.stockCode)
        LabeledContent("持仓数量", value: PortfolioFormatters.integer(position.volume))
        LabeledContent("可用数量", value: PortfolioFormatters.integer(position.availableVolume))
      } header: {
        Text("持仓")
      }

      Section("价格与市值") {
        LabeledContent("成本价", value: PortfolioFormatters.decimal(position.averagePrice))
        LabeledContent("现价", value: PortfolioFormatters.decimal(position.lastPrice))
        LabeledContent("市值", value: PortfolioFormatters.currency(position.marketValue))
        LabeledContent("组合占比", value: PortfolioFormatters.percentage(position.marketValuePercent))
      }

      Section("盈亏") {
        LabeledContent("浮动盈亏") {
          TrendLabel(
            value: PortfolioFormatters.currency(position.profitLoss),
            percentage: PortfolioFormatters.signedPercentage(position.profitRate),
            trend: position.profitLoss
          )
        }
      }

      Section("数据状态") {
        DataFreshnessView(updatedAt: position.updatedAt)
        if let updatedAt = position.updatedAt {
          LabeledContent("源更新时间") {
            Text(updatedAt.formatted(date: .abbreviated, time: .standard))
              .monospacedDigit()
          }
        } else {
          LabeledContent("源更新时间", value: "未知")
        }
        Text("可用数量与盈亏均为后端返回值；客户端不计算真实可卖量或成交状态。")
          .font(.footnote)
          .foregroundStyle(QuantXTheme.secondaryText)
      }
    }
    .navigationTitle(position.displayName)
    .navigationBarTitleDisplayMode(.inline)
  }
}
