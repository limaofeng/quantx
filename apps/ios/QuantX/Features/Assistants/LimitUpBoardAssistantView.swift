import SwiftUI

struct LimitUpBoardAssistantView: View {
  @EnvironmentObject private var model: AppModel
  @State private var selectedRunID: String?
  @State private var approvalPreview: TradeApprovalPreview?
  @State private var approvalRequestIntentID: String?
  @State private var approvalRequestError: String?

  var body: some View {
    content
      .background(QuantXTheme.canvasBackground)
      .navigationTitle("打板助手")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .topBarTrailing) {
          Button {
            Task { await model.refreshLimitUpBoard(runID: selectedRunID) }
          } label: {
            if model.limitUpBoardRefreshInProgress {
              ProgressView()
            } else {
              Image(systemName: "arrow.clockwise")
            }
          }
          .frame(minWidth: 44, minHeight: 44)
          .disabled(model.limitUpBoardRefreshInProgress)
          .accessibilityLabel("刷新打板助手")
        }
      }
      .task {
        if selectedRunID == nil {
          selectedRunID = model.limitUpStrategyInstances.first?.id
        }
        if case .idle = model.limitUpBoardState {
          await model.refreshLimitUpBoard(runID: selectedRunID)
        }
      }
      .onChange(of: selectedRunID) { _, runID in
        guard let runID else { return }
        Task { await model.refreshLimitUpBoard(runID: runID) }
      }
      .sheet(item: $approvalPreview) { preview in
        TradeApprovalSheet(preview: preview) {
          approvalPreview = nil
        }
        .environmentObject(model)
      }
      .alert(
        "无法获取交易预览",
        isPresented: Binding(
          get: { approvalRequestError != nil },
          set: { if !$0 { approvalRequestError = nil } }
        )
      ) {
        Button("知道了", role: .cancel) {}
      } message: {
        Text(approvalRequestError ?? "请刷新后重试")
      }
  }

  @ViewBuilder
  private var content: some View {
    switch model.limitUpBoardState {
    case .unavailable(let reason):
      ContentUnavailableView {
        Label("打板监控不可用", systemImage: "lock.shield.fill")
      } description: {
        Text(reason)
      }
    case .idle, .loading:
      ProgressView("正在读取打板信号与退出计划…")
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    case .noStrategy:
      ContentUnavailableView {
        Label("没有打板策略实例", systemImage: "scope")
      } description: {
        Text("请先在受控管理端创建单标的打板策略实例；移动端不会自行选股或创建实盘策略。")
      }
    case .failed(let message):
      ContentUnavailableView {
        Label("无法读取打板助手", systemImage: "wifi.exclamationmark")
      } description: {
        Text(message)
      } actions: {
        retryButton
      }
    case .loaded(let snapshot, let refreshWarning):
      loaded(snapshot: snapshot, refreshWarning: refreshWarning)
    }
  }

  private func loaded(
    snapshot: LimitUpBoardSnapshot,
    refreshWarning: String?
  ) -> some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 14) {
        if let refreshWarning {
          RefreshWarningView(message: refreshWarning)
        }
        strategyPicker
        safetyCard(snapshot)
        approvalsSection(snapshot.approvals)
        exitPlansSection(snapshot.exitPlans)

        HStack {
          DataFreshnessView(updatedAt: snapshot.fetchedAt)
          Spacer()
          Text("成交以券商回报为准")
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        }
      }
      .padding(16)
    }
    .refreshable {
      await model.refreshLimitUpBoard(runID: selectedRunID)
    }
  }

  @ViewBuilder
  private var strategyPicker: some View {
    if model.limitUpStrategyInstances.count > 1 {
      Picker("打板策略实例", selection: $selectedRunID) {
        ForEach(model.limitUpStrategyInstances) { instance in
          Text("\(instance.displayName) · \(instance.instrumentCode)")
            .tag(Optional(instance.id))
        }
      }
      .pickerStyle(.menu)
      .frame(maxWidth: .infinity, alignment: .leading)
    } else if let instance = model.limitUpStrategyInstances.first {
      Text("\(instance.displayName) · \(instance.instrumentCode)")
        .font(.subheadline.weight(.semibold))
    }
  }

  private func safetyCard(_ snapshot: LimitUpBoardSnapshot) -> some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: 12) {
        HStack(alignment: .top, spacing: 12) {
          Image(systemName: "shield.checkered")
            .font(.title2)
            .foregroundStyle(QuantXTheme.warning)
            .frame(width: 42, height: 42)
            .background(QuantXTheme.warning.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
          VStack(alignment: .leading, spacing: 4) {
            Text("统一执行边界")
              .font(.headline)
            Text("App 只通过服务端短时凭据和本机生物识别确认单笔意图；整手、T+1、涨跌停、可卖量和最终订单状态仍由交易域与券商回报决定。")
              .font(.caption)
              .foregroundStyle(QuantXTheme.secondaryText)
              .fixedSize(horizontal: false, vertical: true)
          }
        }
        HStack(spacing: 16) {
          metric("待确认", snapshot.approvals.count)
          metric("退出计划", snapshot.exitPlans.count)
          metric("待处理订单", snapshot.exitPlans.filter { $0.pendingOrderID != nil }.count)
        }
      }
    }
  }

  @ViewBuilder
  private func approvalsSection(_ approvals: [LimitUpApprovalIntent]) -> some View {
    SectionTitle(title: "待确认入场", subtitle: "超时信号不可补确认")
    if approvals.isEmpty {
      emptyCard("当前没有待确认信号", "策略尚未满足市场、盘口、仓位与风险门禁，或信号已进入执行链路。")
    } else {
      ForEach(approvals) { intent in
        QuantXCard {
          VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
              VStack(alignment: .leading, spacing: 3) {
                Text(intent.instrumentCode)
                  .font(.headline.monospaced())
                Text(intent.reason)
                  .font(.caption)
                  .foregroundStyle(QuantXTheme.secondaryText)
                  .fixedSize(horizontal: false, vertical: true)
              }
              Spacer(minLength: 8)
              StatusBadge(
                title: expiryLabel(intent.approvalExpiresAt),
                systemImage: isExpired(intent.approvalExpiresAt)
                  ? "clock.badge.xmark.fill"
                  : "clock.fill",
                color: isExpired(intent.approvalExpiresAt)
                  ? QuantXTheme.warning
                  : QuantXTheme.accent
              )
            }
            HStack(spacing: 12) {
              labeledValue("信号价", PortfolioFormatters.decimal(intent.signalPrice))
              labeledValue("涨停价", PortfolioFormatters.decimal(intent.limitUpPrice))
              labeledValue(
                "距涨停",
                intent.distanceToLimitTicks.map { "\(Int($0)) 档" } ?? "—"
              )
            }
            HStack(spacing: 12) {
              labeledValue(
                "目标仓位",
                intent.targetPositionPercent.map {
                  PortfolioFormatters.percentage($0 * 100)
                } ?? "—"
              )
              labeledValue("目标数量", intent.targetVolume.map { "\($0.formatted()) 股" } ?? "—")
              labeledValue("置信度", PortfolioFormatters.percentage(intent.confidence * 100))
            }
            approvalAction(intent)
          }
        }
      }
    }
  }

  @ViewBuilder
  private func approvalAction(_ intent: LimitUpApprovalIntent) -> some View {
    if model.canApproveTrades {
      Button {
        Task { await requestApprovalPreview(intent) }
      } label: {
        if approvalRequestIntentID == intent.id {
          ProgressView()
            .frame(maxWidth: .infinity)
        } else {
          Label("核对并安全确认", systemImage: "faceid")
            .frame(maxWidth: .infinity)
        }
      }
      .buttonStyle(.borderedProminent)
      .tint(QuantXTheme.approvalAction)
      .disabled(
        approvalRequestIntentID != nil
          || isExpired(intent.approvalExpiresAt)
      )
    } else {
      Label("当前会话仅可查看；确认需要 trade:approve 独立权限", systemImage: "lock.fill")
        .font(.caption)
        .foregroundStyle(QuantXTheme.secondaryText)
        .fixedSize(horizontal: false, vertical: true)
    }
  }

  private func requestApprovalPreview(_ intent: LimitUpApprovalIntent) async {
    guard approvalRequestIntentID == nil else { return }
    approvalRequestIntentID = intent.id
    defer { approvalRequestIntentID = nil }
    do {
      approvalPreview = try await model.previewStrategyTradeIntentApproval(
        runID: intent.runID,
        intentID: intent.id
      )
    } catch is CancellationError {
      return
    } catch {
      approvalRequestError =
        (error as? LocalizedError)?.errorDescription ?? "打板交易预览暂不可用"
    }
  }

  @ViewBuilder
  private func exitPlansSection(_ plans: [LimitUpExitPlan]) -> some View {
    SectionTitle(title: "活跃退出计划", subtitle: "只有真实买入成交后才会激活")
    if plans.isEmpty {
      emptyCard("当前没有活跃退出计划", "尚未发生有效入场成交，或对应仓位已经完成退出。")
    } else {
      ForEach(plans) { plan in
        QuantXCard {
          VStack(alignment: .leading, spacing: 11) {
            HStack {
              Text(plan.instrumentCode)
                .font(.headline.monospaced())
              Spacer()
              StatusBadge(
                title: plan.status,
                systemImage: plan.pendingOrderID == nil ? "shield.fill" : "clock.arrow.circlepath",
                color: plan.lastExitReason == nil ? QuantXTheme.online : QuantXTheme.warning
              )
            }
            HStack(spacing: 12) {
              labeledValue("剩余", "\(plan.remainingVolume.formatted()) 股")
              labeledValue("持有", "\(plan.holdingTradingDays) 日")
              labeledValue(
                "净收益",
                PortfolioFormatters.signedPercentage(plan.lastNetProfitPercent)
              )
            }
            HStack(spacing: 12) {
              labeledValue("入场均价", PortfolioFormatters.decimal(plan.entryAveragePrice))
              labeledValue("当前价", PortfolioFormatters.decimal(plan.lastPrice))
              labeledValue(
                "峰值收益",
                PortfolioFormatters.signedPercentage(plan.peakNetProfitPercent)
              )
            }
            Text(([plan.t1Policy] + plan.ruleTypes).joined(separator: " · "))
              .font(.caption)
              .foregroundStyle(QuantXTheme.secondaryText)
            if let reason = plan.lastExitReason, !reason.isEmpty {
              Label(reason, systemImage: "exclamationmark.triangle.fill")
                .font(.caption)
                .foregroundStyle(QuantXTheme.warning)
            }
          }
        }
      }
    }
  }

  private func metric(_ title: String, _ value: Int) -> some View {
    VStack(alignment: .leading, spacing: 3) {
      Text(title)
        .font(.caption2)
        .foregroundStyle(QuantXTheme.secondaryText)
      Text(value.formatted())
        .font(.headline)
        .monospacedDigit()
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }

  private func labeledValue(_ title: String, _ value: String) -> some View {
    VStack(alignment: .leading, spacing: 3) {
      Text(title)
        .font(.caption2)
        .foregroundStyle(QuantXTheme.secondaryText)
      Text(value)
        .font(.subheadline.weight(.semibold))
        .monospacedDigit()
        .minimumScaleFactor(0.72)
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }

  private func emptyCard(_ title: String, _ message: String) -> some View {
    QuantXCard {
      ContentUnavailableView {
        Label(title, systemImage: "tray")
      } description: {
        Text(message)
      }
    }
  }

  private var retryButton: some View {
    Button("重新加载") {
      Task { await model.refreshLimitUpBoard(runID: selectedRunID) }
    }
    .buttonStyle(.borderedProminent)
    .controlSize(.large)
    .disabled(model.limitUpBoardRefreshInProgress)
  }

  private func isExpired(_ date: Date?) -> Bool {
    guard let date else { return false }
    return date <= Date()
  }

  private func expiryLabel(_ date: Date?) -> String {
    guard let date else { return "无截止时间" }
    guard date > Date() else { return "已过期" }
    let seconds = max(1, Int(date.timeIntervalSinceNow.rounded(.up)))
    return seconds < 60 ? "剩余 \(seconds) 秒" : "\(date.formatted(date: .omitted, time: .shortened)) 截止"
  }
}
