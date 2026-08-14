import SwiftUI

struct TTradeControlView: View {
  @ObservedObject var store: TTradeControlStore
  let assistantSnapshot: TTradeAssistantSnapshot

  @State private var killReason = ""
  @State private var pauseReason = "移动端主动暂停新入场"
  @State private var showsPauseConfirmation = false

  var body: some View {
    LazyVStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
      if let successMessage = store.successMessage {
        QuantXStatusBanner(
          title: "服务端已确认",
          message: successMessage,
          status: .ready
        )
      }
      if let errorMessage = store.errorMessage {
        QuantXStatusBanner(
          title: "操作未确认为成功",
          message: errorMessage,
          status: .attention
        )
      }
      stateContent
    }
    .task {
      if case .idle = store.state { await store.refresh() }
    }
    .sheet(
      item: Binding(
        get: { store.pendingControl },
        set: { value in
          if value == nil { store.dismissPendingControl() }
        }
      )
    ) { preview in
      TTradeControlConfirmationSheet(store: store, preview: preview)
    }
    .alert("仅停止新入场", isPresented: $showsPauseConfirmation) {
      TextField("暂停原因", text: $pauseReason)
      Button("取消", role: .cancel) {}
      Button("停止新入场", role: .destructive) {
        Task { try? await store.pauseEntries(reason: pauseReason) }
      }
    } message: {
      Text("此操作不需要生物确认。它只停止新的做 T 买入；现有批次的退出保护会继续运行。")
    }
  }

  @ViewBuilder
  private var stateContent: some View {
    switch store.state {
    case .idle, .loading:
      QuantXCard {
        ProgressView("正在读取账户级安全状态…")
          .frame(maxWidth: .infinity, minHeight: 120)
      }
    case .unavailable(let message):
      unavailable(title: "做 T 控制不可用", message: message)
    case .failed(let message):
      unavailable(title: "无法读取做 T 安全状态", message: message)
    case .loaded(let snapshot, let refreshWarning):
      if let refreshWarning { RefreshWarningView(message: refreshWarning) }
      rolloutCard(snapshot)
      projectionCard(snapshot)
      connectionCard(snapshot)
      readinessCard(snapshot)
      controlActions(snapshot)
      HStack {
        DataFreshnessView(updatedAt: snapshot.checkedAt)
        Spacer()
        Text("服务端生产门禁")
          .font(.caption)
          .foregroundStyle(QuantXTheme.secondaryText)
      }
    }
  }

  private func rolloutCard(_ snapshot: TTradeControlSnapshot) -> some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        HStack(alignment: .top, spacing: QuantXTheme.Spacing.medium) {
          VStack(alignment: .leading, spacing: 4) {
            Text("账户级灰度状态")
              .font(.headline)
            Text("主账户 \(TTradeControlPrivacy.maskedAccount(snapshot.accountID))")
              .font(.caption.monospaced())
              .foregroundStyle(QuantXTheme.secondaryText)
          }
          Spacer()
          StatusBadge(
            title: snapshot.killSwitch ? "已熔断" : snapshot.stage,
            systemImage: snapshot.killSwitch
              ? "exclamationmark.octagon.fill"
              : "shield.lefthalf.filled",
            color: snapshot.killSwitch ? QuantXTheme.critical : QuantXTheme.accent
          )
        }

        ViewThatFits(in: .horizontal) {
          HStack(spacing: QuantXTheme.Spacing.small) {
            metric("策略版本", snapshot.policyVersion.formatted())
            metric("监控模式", snapshot.monitorMode)
            metric("安全状态", snapshot.status)
          }
          VStack(spacing: QuantXTheme.Spacing.small) {
            metric("策略版本", snapshot.policyVersion.formatted())
            metric("监控模式", snapshot.monitorMode)
            metric("安全状态", snapshot.status)
          }
        }

        Label {
          VStack(alignment: .leading, spacing: 3) {
            Text(snapshot.controlledWindowActive ? "受控窗口已建立" : "受控窗口未建立")
              .font(.subheadline.weight(.semibold))
            Text(
              snapshot.controlledWindowSnapshotID.map { "绑定快照 \($0)" }
                ?? "Canary / LIVE 激活前必须由服务端建立受控窗口"
            )
            .font(.caption.monospaced())
            .foregroundStyle(QuantXTheme.secondaryText)
            .textSelection(.enabled)
          }
        } icon: {
          Image(
            systemName: snapshot.controlledWindowActive
              ? "lock.shield.fill"
              : "lock.open.trianglebadge.exclamationmark"
          )
          .foregroundStyle(
            snapshot.controlledWindowActive ? QuantXTheme.online : QuantXTheme.warning
          )
        }
      }
    }
  }

  private func projectionCard(_ snapshot: TTradeControlSnapshot) -> some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        HStack {
          Text("账户业务投影")
            .font(.headline)
          Spacer()
          DataFreshnessView(
            updatedAt: snapshot.projectionGeneratedAt ?? assistantSnapshot.projectionGeneratedAt
          )
        }
        ViewThatFits(in: .horizontal) {
          HStack(spacing: QuantXTheme.Spacing.small) {
            metric("活跃批次", assistantSnapshot.activeBatchCount.formatted())
            metric("退出中", assistantSnapshot.drainingCount.formatted())
            metric("待确认信号", assistantSnapshot.pendingSignalCount.formatted())
          }
          VStack(spacing: QuantXTheme.Spacing.small) {
            metric("活跃批次", assistantSnapshot.activeBatchCount.formatted())
            metric("退出中", assistantSnapshot.drainingCount.formatted())
            metric("待确认信号", assistantSnapshot.pendingSignalCount.formatted())
          }
        }
        if !snapshot.positionSnapshotComplete || snapshot.positionSnapshotError != nil {
          Label(
            snapshot.positionSnapshotError ?? "持仓快照尚未完整",
            systemImage: "exclamationmark.triangle.fill"
          )
          .font(.caption)
          .foregroundStyle(QuantXTheme.warning)
          .fixedSize(horizontal: false, vertical: true)
        }
      }
    }
  }

  private func connectionCard(_ snapshot: TTradeControlSnapshot) -> some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        Text("执行链路")
          .font(.headline)
        statusRow(
          title: "Engine",
          value: snapshot.engineStatus,
          passed: snapshot.engineStatus == "READY"
        )
        statusRow(
          title: "QMT Agent",
          value: [snapshot.agentStatus, snapshot.agentMode].filter { !$0.isEmpty }
            .joined(separator: " · "),
          passed: snapshot.agentStatus == "READY" && snapshot.agentMode == "LIVE"
        )
        statusRow(
          title: "协议",
          value: snapshot.protocolVersion.isEmpty ? "未上报" : snapshot.protocolVersion,
          passed: snapshot.protocolVersion == "1.1"
        )
        statusRow(
          title: "账户对账",
          value: snapshot.reconcileStatus,
          passed: snapshot.reconcileStatus == "READY"
        )
        Divider()
        labeledText(
          "安全快照",
          snapshot.snapshotID ?? "当前没有完整快照",
          monospaced: true
        )
        if let snapshotAt = snapshot.snapshotAt {
          labeledText(
            "快照时间",
            snapshotAt.formatted(date: .abbreviated, time: .standard)
          )
        }
        labeledText(
          "手工交易共存",
          snapshot.manualCoexistence ? "已检测到，需要核对外部活动" : "未检测到"
        )
      }
    }
  }

  private func readinessCard(_ snapshot: TTradeControlSnapshot) -> some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        HStack {
          Text("生产就绪检查")
            .font(.headline)
          Spacer()
          StatusBadge(
            title: snapshot.ready ? "已就绪" : "未就绪",
            systemImage: snapshot.ready
              ? "checkmark.shield.fill"
              : "exclamationmark.shield.fill",
            color: snapshot.ready ? QuantXTheme.online : QuantXTheme.warning
          )
        }
        if !snapshot.blockedReasons.isEmpty {
          ForEach(Array(snapshot.blockedReasons.enumerated()), id: \.offset) { _, reason in
            Label(reason, systemImage: "exclamationmark.circle.fill")
              .font(.caption)
              .foregroundStyle(QuantXTheme.warning)
              .fixedSize(horizontal: false, vertical: true)
          }
        }
        if snapshot.checks.isEmpty {
          Text("服务端未返回普通生产检查；紧急熔断仍可独立预览。")
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        } else {
          ForEach(snapshot.checks) { check in
            Label {
              VStack(alignment: .leading, spacing: 2) {
                Text(check.code)
                  .font(.subheadline.weight(.semibold).monospaced())
                Text("\(check.scope) · \(check.message)")
                  .font(.caption)
                  .foregroundStyle(QuantXTheme.secondaryText)
                  .fixedSize(horizontal: false, vertical: true)
              }
            } icon: {
              Image(systemName: check.passed ? "checkmark.circle.fill" : "xmark.circle.fill")
                .foregroundStyle(check.passed ? QuantXTheme.online : QuantXTheme.warning)
                .accessibilityLabel(check.passed ? "通过" : "未通过")
            }
          }
        }
      }
    }
  }

  private func controlActions(_ snapshot: TTradeControlSnapshot) -> some View {
    VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
      SectionTitle(
        title: "账户安全控制",
        subtitle: "所有高风险动作都由服务端预览、逐项核对和本机生物确认"
      )

      ForEach([
        TTradeSafetyAction.beginControlledWindow,
        .activateCanary,
        .activateLive,
      ]) { action in
        actionCard(action, snapshot: snapshot)
      }

      QuantXCard {
        VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
          Label("暂停新入场", systemImage: "pause.circle.fill")
            .font(.headline)
            .foregroundStyle(QuantXTheme.warning)
          Text("风险降低操作：仅停止新的买入，现有退出保护继续运行。")
            .font(.subheadline)
            .foregroundStyle(QuantXTheme.secondaryText)
          Button("核对暂停影响") {
            showsPauseConfirmation = true
          }
          .buttonStyle(.bordered)
          .controlSize(.large)
          .frame(minHeight: 44)
          .disabled(store.operationInProgress || store.pauseUnavailableReason != nil)
          .accessibilityHint("此操作不要求生物认证")
          if let reason = store.pauseUnavailableReason {
            unavailableCaption(reason)
          }
        }
      }

      QuantXCard {
        VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
          Label("紧急熔断", systemImage: "exclamationmark.octagon.fill")
            .font(.headline)
            .foregroundStyle(QuantXTheme.critical)
          Text("普通 Agent、快照或生产门禁异常时仍允许请求熔断预览。")
            .font(.subheadline)
            .foregroundStyle(QuantXTheme.secondaryText)
          TextField("必填：处置原因", text: $killReason, axis: .vertical)
            .textFieldStyle(.roundedBorder)
            .lineLimit(2...4)
            .accessibilityLabel("紧急熔断原因")
          Button {
            requestPreview(.killSwitch, reason: killReason)
          } label: {
            actionLabel(.killSwitch)
          }
          .buttonStyle(.borderedProminent)
          .tint(QuantXTheme.critical)
          .controlSize(.large)
          .frame(minHeight: 44)
          .disabled(
            store.operationInProgress
              || killReason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
              || store.controlUnavailableReason(for: .killSwitch) != nil
          )
          .accessibilityHint("打开服务端熔断预览；不会直接触发熔断")
          if let reason = store.controlUnavailableReason(for: .killSwitch) {
            unavailableCaption(reason)
          }
        }
      }
    }
  }

  private func actionCard(
    _ action: TTradeSafetyAction,
    snapshot: TTradeControlSnapshot
  ) -> some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        Label(action.title, systemImage: actionSystemImage(action))
          .font(.headline)
          .foregroundStyle(
            action == .activateLive ? QuantXTheme.approvalAction : QuantXTheme.accent)
        Text(action.detail)
          .font(.subheadline)
          .foregroundStyle(QuantXTheme.secondaryText)
          .fixedSize(horizontal: false, vertical: true)
        Button {
          requestPreview(action)
        } label: {
          actionLabel(action)
        }
        .buttonStyle(.borderedProminent)
        .tint(action == .activateLive ? QuantXTheme.approvalAction : QuantXTheme.accent)
        .controlSize(.large)
        .frame(minHeight: 44)
        .disabled(
          store.operationInProgress
            || store.controlUnavailableReason(for: action) != nil
        )
        .accessibilityHint("请求服务端预览；预览成功后才可进行生物确认")
        if let reason = store.controlUnavailableReason(for: action) {
          unavailableCaption(reason)
        }
      }
    }
  }

  private func actionLabel(_ action: TTradeSafetyAction) -> some View {
    Group {
      if store.requestedAction == action {
        ProgressView()
          .frame(maxWidth: .infinity)
      } else {
        Label("服务端预览并核对", systemImage: "faceid")
          .frame(maxWidth: .infinity)
      }
    }
  }

  private func requestPreview(_ action: TTradeSafetyAction, reason: String = "") {
    Task {
      try? await store.preview(action: action, reason: reason)
    }
  }

  private func metric(_ title: String, _ value: String) -> some View {
    VStack(alignment: .leading, spacing: 3) {
      Text(title)
        .font(.caption2)
        .foregroundStyle(QuantXTheme.secondaryText)
      Text(value)
        .font(.subheadline.weight(.semibold))
        .monospacedDigit()
        .minimumScaleFactor(0.75)
    }
    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
    .padding(.horizontal, 10)
    .padding(.vertical, 8)
    .background(QuantXTheme.elevatedBackground, in: RoundedRectangle(cornerRadius: 10))
    .accessibilityElement(children: .combine)
  }

  private func statusRow(title: String, value: String, passed: Bool) -> some View {
    HStack(alignment: .firstTextBaseline, spacing: QuantXTheme.Spacing.small) {
      Image(systemName: passed ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
        .foregroundStyle(passed ? QuantXTheme.online : QuantXTheme.warning)
        .accessibilityHidden(true)
      Text(title)
        .font(.subheadline)
      Spacer()
      Text(value.isEmpty ? "未知" : value)
        .font(.subheadline.weight(.semibold).monospaced())
        .foregroundStyle(passed ? Color.primary : QuantXTheme.warning)
        .multilineTextAlignment(.trailing)
    }
    .accessibilityElement(children: .combine)
  }

  private func labeledText(_ title: String, _ value: String, monospaced: Bool = false) -> some View
  {
    VStack(alignment: .leading, spacing: 3) {
      Text(title)
        .font(.caption)
        .foregroundStyle(QuantXTheme.secondaryText)
      Text(value)
        .font(monospaced ? .caption.monospaced() : .subheadline)
        .textSelection(.enabled)
        .fixedSize(horizontal: false, vertical: true)
    }
  }

  private func unavailableCaption(_ reason: String) -> some View {
    Label(reason, systemImage: "lock.fill")
      .font(.caption)
      .foregroundStyle(QuantXTheme.secondaryText)
      .fixedSize(horizontal: false, vertical: true)
  }

  private func unavailable(title: String, message: String) -> some View {
    QuantXCard {
      ContentUnavailableView {
        Label(title, systemImage: "lock.shield.fill")
      } description: {
        Text(message)
      } actions: {
        Button("重新加载") { Task { await store.refresh() } }
          .buttonStyle(.borderedProminent)
          .disabled(store.refreshInProgress)
      }
    }
  }

  private func actionSystemImage(_ action: TTradeSafetyAction) -> String {
    switch action {
    case .beginControlledWindow: "lock.shield.fill"
    case .activateCanary: "gauge.with.dots.needle.33percent"
    case .activateLive: "bolt.shield.fill"
    case .killSwitch: "exclamationmark.octagon.fill"
    }
  }
}

private struct TTradeControlConfirmationSheet: View {
  @Environment(\.dismiss) private var dismiss
  @ObservedObject var store: TTradeControlStore
  let preview: TTradeControlPreviewTicket

  var body: some View {
    NavigationStack {
      ScrollView {
        LazyVStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
          QuantXStatusBanner(
            title: preview.action.title,
            message: "核对以下服务端快照后，每次都需要 Face ID 或 Touch ID。确认只应用账户控制，不代表委托已报送或成交。",
            status: preview.action == .killSwitch ? .blocked : .attention
          )
          exactContext
          checks
          warnings
          expiration
          confirmButton
        }
        .padding(QuantXTheme.Spacing.large)
      }
      .background(QuantXTheme.canvasBackground)
      .navigationTitle("核对做 T 控制")
      .navigationBarTitleDisplayMode(.inline)
      .interactiveDismissDisabled(store.operationInProgress)
      .toolbar {
        ToolbarItem(placement: .cancellationAction) {
          Button("取消") {
            store.dismissPendingControl()
            dismiss()
          }
          .disabled(store.operationInProgress)
        }
      }
    }
  }

  private var exactContext: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        Text("精确绑定")
          .font(.headline)
        row("动作", preview.action.rawValue)
        row("主账户", TTradeControlPrivacy.maskedAccount(preview.accountID))
        row("当前阶段", preview.currentStage)
        row("策略版本", preview.policyVersion.formatted())
        row("安全快照", preview.snapshotID.isEmpty ? "熔断不依赖快照" : preview.snapshotID)
        row("目标阶段", preview.targetStage?.rawValue ?? "无")
        row("控制原因", preview.reason)
        row("就绪指纹", preview.readinessFingerprint)
      }
    }
  }

  @ViewBuilder
  private var checks: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        Text("服务端预览检查")
          .font(.headline)
        if preview.checks.isEmpty {
          Label(
            "紧急熔断不依赖普通生产就绪检查",
            systemImage: "exclamationmark.octagon.fill"
          )
          .font(.subheadline)
          .foregroundStyle(QuantXTheme.critical)
        } else {
          ForEach(preview.checks) { check in
            Label {
              VStack(alignment: .leading, spacing: 2) {
                Text(check.code)
                  .font(.subheadline.weight(.semibold).monospaced())
                Text("\(check.scope) · \(check.message)")
                  .font(.caption)
                  .foregroundStyle(QuantXTheme.secondaryText)
              }
            } icon: {
              Image(systemName: check.passed ? "checkmark.circle.fill" : "xmark.circle.fill")
                .foregroundStyle(check.passed ? QuantXTheme.online : QuantXTheme.warning)
            }
          }
        }
      }
    }
  }

  private var warnings: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.small) {
        Text("确认前须知")
          .font(.headline)
        ForEach(Array(preview.warnings.enumerated()), id: \.offset) { _, warning in
          Label(warning, systemImage: "exclamationmark.triangle.fill")
            .font(.caption)
            .foregroundStyle(QuantXTheme.warning)
            .fixedSize(horizontal: false, vertical: true)
        }
      }
    }
  }

  private var expiration: some View {
    TimelineView(.periodic(from: .now, by: 1)) { context in
      let remaining = max(0, Int(preview.expiresAt.timeIntervalSince(context.date)))
      Label(
        remaining > 0 ? "一次性凭据将在 \(remaining) 秒后失效" : "一次性凭据已失效",
        systemImage: remaining > 0 ? "timer" : "timer.square"
      )
      .font(.caption.weight(.semibold))
      .foregroundStyle(remaining > 0 ? QuantXTheme.warning : QuantXTheme.critical)
      .accessibilityElement(children: .combine)
    }
  }

  private var confirmButton: some View {
    TimelineView(.periodic(from: .now, by: 1)) { context in
      Button {
        Task {
          do {
            try await store.confirm(preview)
            dismiss()
          } catch {
            // The store keeps a privacy-safe, user-facing error and never marks
            // an uncertain transport result as successful.
          }
        }
      } label: {
        if store.operationInProgress {
          ProgressView()
            .frame(maxWidth: .infinity)
        } else {
          Label("生物确认并提交", systemImage: "faceid")
            .frame(maxWidth: .infinity)
        }
      }
      .buttonStyle(.borderedProminent)
      .tint(preview.action == .killSwitch ? QuantXTheme.critical : QuantXTheme.approvalAction)
      .controlSize(.large)
      .frame(minHeight: 52)
      .disabled(store.operationInProgress || preview.expiresAt <= context.date)
      .accessibilityHint("通过本机认证后消费此一次性服务端凭据")
    }
  }

  private func row(_ title: String, _ value: String) -> some View {
    VStack(alignment: .leading, spacing: 3) {
      Text(title)
        .font(.caption)
        .foregroundStyle(QuantXTheme.secondaryText)
      Text(value)
        .font(.subheadline.monospaced())
        .textSelection(.enabled)
        .fixedSize(horizontal: false, vertical: true)
    }
  }
}
