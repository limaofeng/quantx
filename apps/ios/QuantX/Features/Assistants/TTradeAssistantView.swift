import SwiftUI

struct TTradeAssistantView: View {
  private enum Scope: String, CaseIterable, Identifiable {
    case positions
    case batches
    case signals
    case readiness
    case control

    var id: Self { self }

    var title: String {
      switch self {
      case .positions: "仓位"
      case .batches: "批次"
      case .signals: "信号"
      case .readiness: "门禁"
      case .control: "控制"
      }
    }
  }

  @EnvironmentObject private var model: AppModel
  @State private var scope: Scope = .positions
  @State private var approvalPreview: TradeApprovalPreview?
  @State private var approvalRequestIntentID: String?
  @State private var approvalRequestError: String?

  var body: some View {
    content
      .background(QuantXTheme.canvasBackground)
      .navigationTitle("做T助手")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .topBarTrailing) {
          Button {
            Task {
              await model.refreshTTradeAssistant()
              if scope == .control { await model.tTradeControlStore.refresh() }
            }
          } label: {
            if model.tTradeAssistantRefreshInProgress {
              ProgressView()
            } else {
              Image(systemName: "arrow.clockwise")
            }
          }
          .frame(minWidth: 44, minHeight: 44)
          .disabled(model.tTradeAssistantRefreshInProgress)
          .accessibilityLabel("刷新做T助手")
        }
      }
      .task {
        if case .idle = model.tTradeAssistantState {
          await model.refreshTTradeAssistant()
        }
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
    switch model.tTradeAssistantState {
    case .unavailable(let reason):
      unavailable(title: "做T监控不可用", message: reason, systemImage: "lock.shield.fill")
    case .idle, .loading:
      ProgressView("正在读取做T账户投影…")
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    case .noAccount:
      unavailable(
        title: "没有可用账户",
        message: "当前会话没有后端确认的授权账户。",
        systemImage: "person.crop.circle.badge.questionmark"
      )
    case .failed(let message):
      ContentUnavailableView {
        Label("无法读取做T助手", systemImage: "wifi.exclamationmark")
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
    snapshot: TTradeAssistantSnapshot,
    refreshWarning: String?
  ) -> some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 14) {
        if let refreshWarning {
          RefreshWarningView(message: refreshWarning)
        }
        statusCard(snapshot)

        Picker("做T数据范围", selection: $scope) {
          ForEach(Scope.allCases) { item in
            Text(item.title).tag(item)
          }
        }
        .pickerStyle(.segmented)

        switch scope {
        case .positions:
          holdingsSection(snapshot)
        case .batches:
          batchesSection(snapshot)
        case .signals:
          signalsSection(snapshot)
        case .readiness:
          readinessSection(snapshot)
        case .control:
          TTradeControlView(
            store: model.tTradeControlStore,
            assistantSnapshot: snapshot
          )
        }

        HStack {
          DataFreshnessView(updatedAt: snapshot.fetchedAt)
          Spacer()
          Text(model.canApproveTrades ? "安全确认已启用" : "只读监控")
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        }
      }
      .padding(16)
    }
    .refreshable {
      await model.refreshTTradeAssistant()
      if scope == .control { await model.tTradeControlStore.refresh() }
    }
  }

  private func statusCard(_ snapshot: TTradeAssistantSnapshot) -> some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: 14) {
        HStack(alignment: .top, spacing: 12) {
          VStack(alignment: .leading, spacing: 4) {
            Text("账户级做T监控")
              .font(.headline)
            Text("\(snapshot.mode.uppercased()) · \(snapshot.rolloutStage)")
              .font(.caption)
              .foregroundStyle(QuantXTheme.secondaryText)
          }
          Spacer()
          StatusBadge(
            title: snapshot.killSwitch ? "已熔断" : (snapshot.enabled ? "监控中" : "未启用"),
            systemImage: snapshot.killSwitch
              ? "exclamationmark.octagon.fill"
              : (snapshot.enabled ? "eye.fill" : "pause.circle.fill"),
            color: snapshot.killSwitch
              ? QuantXTheme.warning
              : (snapshot.enabled ? QuantXTheme.online : QuantXTheme.secondaryText)
          )
        }

        HStack(spacing: 8) {
          metric("监控", snapshot.monitoredCount)
          metric("可用", snapshot.eligibleCount)
          metric("待确认", snapshot.pendingSignalCount)
          metric("活跃批次", snapshot.activeBatchCount)
        }

        if let lastError = snapshot.lastError, !lastError.isEmpty {
          Label(lastError, systemImage: "exclamationmark.triangle.fill")
            .font(.caption)
            .foregroundStyle(QuantXTheme.warning)
            .fixedSize(horizontal: false, vertical: true)
        }
        if !snapshot.blockedReasons.isEmpty {
          Text(snapshot.blockedReasons.joined(separator: " · "))
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
            .fixedSize(horizontal: false, vertical: true)
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

  @ViewBuilder
  private func holdingsSection(_ snapshot: TTradeAssistantSnapshot) -> some View {
    if snapshot.holdings.isEmpty {
      emptyCard("暂无做T仓位", "服务端当前没有返回被监控的账户持仓。")
    } else {
      ForEach(snapshot.holdings) { holding in
        QuantXCard {
          VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
              VStack(alignment: .leading, spacing: 3) {
                Text(holding.instrumentName.isEmpty ? holding.stockCode : holding.instrumentName)
                  .font(.headline)
                Text(holding.stockCode)
                  .font(.caption.monospaced())
                  .foregroundStyle(QuantXTheme.secondaryText)
              }
              Spacer()
              StatusBadge(
                title: holding.ignored ? "已忽略" : (holding.eligible ? "可监控" : "受限"),
                systemImage: holding.eligible ? "checkmark.shield.fill" : "shield.slash.fill",
                color: holding.eligible ? QuantXTheme.online : QuantXTheme.warning
              )
            }

            HStack(spacing: 16) {
              labeledValue("持仓", "\(holding.volume.formatted()) 股")
              labeledValue("可用", "\(holding.availableVolume.formatted()) 股")
              if let session = holding.session {
                labeledValue(
                  "净收益",
                  PortfolioFormatters.signedPercentage(session.lastNetProfitPercent)
                )
              }
            }
            if !holding.reason.isEmpty {
              Text(holding.reason)
                .font(.caption)
                .foregroundStyle(QuantXTheme.secondaryText)
            }
          }
        }
      }
    }
  }

  @ViewBuilder
  private func batchesSection(_ snapshot: TTradeAssistantSnapshot) -> some View {
    if snapshot.batches.isEmpty {
      emptyCard("暂无做T批次", "没有已进入委托与成交状态流的做T批次。")
    } else {
      ForEach(snapshot.batches) { batch in
        QuantXCard {
          VStack(alignment: .leading, spacing: 10) {
            HStack {
              Text(batch.stockCode)
                .font(.headline.monospaced())
              Spacer()
              Text(batch.status)
                .font(.caption.weight(.semibold))
                .foregroundStyle(statusColor(batch.status))
            }
            HStack(spacing: 14) {
              labeledValue("目标", "\(batch.targetVolume.formatted()) 股")
              labeledValue("入场成交", "\(batch.entryFilledVolume.formatted()) 股")
              labeledValue("活跃", "\(batch.activeVolume.formatted()) 股")
            }
            HStack(spacing: 14) {
              labeledValue("现价", PortfolioFormatters.decimal(batch.lastPrice))
              labeledValue(
                "净收益",
                PortfolioFormatters.signedPercentage(batch.lastNetProfitPercent)
              )
              labeledValue("退出成交", "\(batch.exitFilledVolume.formatted()) 股")
            }
            if let reason = batch.exceptionReason ?? batch.exitReason, !reason.isEmpty {
              Text(reason)
                .font(.caption)
                .foregroundStyle(QuantXTheme.warning)
            }
          }
        }
      }
      if snapshot.batchesHaveMore {
        Text("仅显示最近 20 个批次")
          .font(.caption)
          .foregroundStyle(QuantXTheme.secondaryText)
      }
    }
  }

  @ViewBuilder
  private func signalsSection(_ snapshot: TTradeAssistantSnapshot) -> some View {
    if snapshot.signals.isEmpty {
      emptyCard("暂无做T信号", "当前没有等待确认或已处理的买入信号。")
    } else {
      ForEach(snapshot.signals) { signal in
        QuantXCard {
          VStack(alignment: .leading, spacing: 9) {
            HStack {
              Text(signal.stockCode)
                .font(.headline.monospaced())
              Spacer()
              Text(signal.status)
                .font(.caption.weight(.semibold))
                .foregroundStyle(statusColor(signal.status))
            }
            HStack(spacing: 14) {
              labeledValue("信号价", PortfolioFormatters.decimal(signal.signalPrice))
              labeledValue(
                "回撤",
                PortfolioFormatters.signedPercentage(signal.pullbackPercent)
              )
              labeledValue("数量", "\(signal.requestedVolume.formatted()) 股")
            }
            if !signal.statusReason.isEmpty {
              Text(signal.statusReason)
                .font(.caption)
                .foregroundStyle(QuantXTheme.secondaryText)
            }
            if signal.status.uppercased() == "AWAITING_APPROVAL" {
              approvalAction(signal: signal, snapshot: snapshot)
            }
          }
        }
      }
      if snapshot.signalsHaveMore {
        Text("仅显示最近 20 条信号")
          .font(.caption)
          .foregroundStyle(QuantXTheme.secondaryText)
      }
    }
  }

  @ViewBuilder
  private func approvalAction(
    signal: TTradeSignalItem,
    snapshot: TTradeAssistantSnapshot
  ) -> some View {
    if model.canApproveTrades {
      Button {
        Task { await requestApprovalPreview(signal) }
      } label: {
        if approvalRequestIntentID == signal.id {
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
          || snapshot.killSwitch
          || !snapshot.canApprove
          || signal.expiresAt.map { $0 <= Date() } == true
      )
    } else {
      Label("当前会话仅可查看；确认需要 trade:approve 独立权限", systemImage: "lock.fill")
        .font(.caption)
        .foregroundStyle(QuantXTheme.secondaryText)
        .fixedSize(horizontal: false, vertical: true)
    }
  }

  private func requestApprovalPreview(_ signal: TTradeSignalItem) async {
    guard approvalRequestIntentID == nil else { return }
    approvalRequestIntentID = signal.id
    defer { approvalRequestIntentID = nil }
    do {
      approvalPreview = try await model.previewTTradeEntryApproval(
        runID: signal.runID,
        intentID: signal.id
      )
    } catch is CancellationError {
      return
    } catch {
      approvalRequestError =
        (error as? LocalizedError)?.errorDescription ?? "做T交易预览暂不可用"
    }
  }

  @ViewBuilder
  private func readinessSection(_ snapshot: TTradeAssistantSnapshot) -> some View {
    if let readiness = snapshot.readiness {
      QuantXCard {
        VStack(alignment: .leading, spacing: 10) {
          HStack {
            Text("生产就绪门禁")
              .font(.headline)
            Spacer()
            StatusBadge(
              title: readiness.ready ? "已就绪" : "未就绪",
              systemImage: readiness.ready
                ? "checkmark.shield.fill" : "exclamationmark.shield.fill",
              color: readiness.ready ? QuantXTheme.online : QuantXTheme.warning
            )
          }
          Text("策略版本 \(readiness.policyVersion) · \(readiness.stage)")
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        }
      }
      ForEach(readiness.checks) { check in
        QuantXCard {
          Label {
            VStack(alignment: .leading, spacing: 3) {
              Text(check.code)
                .font(.subheadline.weight(.semibold))
              Text(check.message)
                .font(.caption)
                .foregroundStyle(QuantXTheme.secondaryText)
            }
          } icon: {
            Image(systemName: check.passed ? "checkmark.circle.fill" : "xmark.circle.fill")
              .foregroundStyle(check.passed ? QuantXTheme.online : QuantXTheme.warning)
          }
        }
      }
    } else {
      emptyCard("暂无就绪检查", "服务端未返回做T生产门禁快照。")
    }
  }

  private func labeledValue(_ title: String, _ value: String) -> some View {
    VStack(alignment: .leading, spacing: 3) {
      Text(title)
        .font(.caption2)
        .foregroundStyle(QuantXTheme.secondaryText)
      Text(value)
        .font(.subheadline.weight(.semibold))
        .monospacedDigit()
        .minimumScaleFactor(0.75)
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

  private func unavailable(title: String, message: String, systemImage: String) -> some View {
    ContentUnavailableView {
      Label(title, systemImage: systemImage)
    } description: {
      Text(message)
    }
  }

  private var retryButton: some View {
    Button("重新加载") {
      Task { await model.refreshTTradeAssistant() }
    }
    .buttonStyle(.borderedProminent)
    .controlSize(.large)
    .disabled(model.tTradeAssistantRefreshInProgress)
  }

  private func statusColor(_ status: String) -> Color {
    let normalized = status.uppercased()
    if normalized.contains("ERROR") || normalized.contains("REJECT")
      || normalized.contains("CANCEL")
    {
      return QuantXTheme.warning
    }
    if normalized.contains("ACTIVE") || normalized.contains("FILLED")
      || normalized.contains("COMPLETED")
    {
      return QuantXTheme.online
    }
    return QuantXTheme.accent
  }
}
