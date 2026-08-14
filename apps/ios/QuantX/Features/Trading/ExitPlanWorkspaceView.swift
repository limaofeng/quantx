import SwiftUI

struct ExitPlanWorkspaceView: View {
  @ObservedObject var store: ExitPlanWorkspace

  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: QuantXTheme.Spacing.large) {
        QuantXStatusBanner(
          title: "退出保护来自服务端真源",
          message: "这里展示统一 ExitPlan；自动实盘授权只覆盖一次预览中签名的账户、标的、规则、保护量、版本和安全快照。",
          status: .attention
        )
        content
      }
      .padding(QuantXTheme.Spacing.large)
    }
    .background(QuantXTheme.canvasBackground)
    .refreshable { await store.refresh() }
    .task {
      if case .idle = store.listState { await store.refresh() }
    }
  }

  @ViewBuilder
  private var content: some View {
    switch store.listState {
    case .unavailable(let message):
      unavailable(message)
    case .idle, .loading:
      loading
    case .failed(let message):
      failure(message)
    case .loaded(let snapshot, let warning):
      if let warning {
        QuantXStatusBanner(title: "刷新未完成", message: warning, status: .attention)
      }
      summary(snapshot)
      QuantXStatusBanner(
        title: "PAPER 配置当前只读",
        message: store.paperConfigurationMessage,
        status: .unavailable
      )
      if snapshot.plans.isEmpty {
        empty
      } else {
        ForEach(snapshot.plans) { plan in
          NavigationLink {
            ExitPlanDetailView(store: store, initialPlan: plan)
          } label: {
            ExitPlanRow(plan: plan)
          }
          .buttonStyle(.plain)
          .accessibilityHint("查看规则、保护数量、冲突、审计和精确授权状态")
        }
      }
    }
  }

  private var loading: some View {
    QuantXCard {
      HStack(spacing: QuantXTheme.Spacing.medium) {
        ProgressView()
        Text("正在同步当前主账户的退出计划…")
          .foregroundStyle(QuantXTheme.secondaryText)
      }
      .frame(minHeight: 64)
    }
    .accessibilityElement(children: .combine)
  }

  private func unavailable(_ message: String) -> some View {
    ContentUnavailableView {
      Label("退出计划不可用", systemImage: "lock.shield")
    } description: {
      Text(message)
    } actions: {
      Button("重新检查") { Task { await store.refresh() } }
        .buttonStyle(.borderedProminent)
    }
  }

  private func failure(_ message: String) -> some View {
    ContentUnavailableView {
      Label("无法读取退出计划", systemImage: "exclamationmark.arrow.triangle.2.circlepath")
    } description: {
      Text(message)
    } actions: {
      Button("重试") { Task { await store.refresh() } }
        .buttonStyle(.borderedProminent)
    }
  }

  private var empty: some View {
    ContentUnavailableView {
      Label("没有退出计划", systemImage: "shield")
    } description: {
      Text("当前主账户没有服务端退出保护。此页面不会用本地示例或兼容写接口创建计划。")
    } actions: {
      Button("刷新") { Task { await store.refresh() } }
        .buttonStyle(.bordered)
    }
  }

  private func summary(_ snapshot: ExitPlanListSnapshot) -> some View {
    let live = snapshot.plans.filter { $0.executionMode == .live }
    let awaiting = live.filter {
      if case .authorized = $0.authorizationState { return false }
      return $0.status.isAuthorizable && $0.remainingVolume > 0
    }
    return QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(
          title: "退出计划",
          subtitle: "主账户 •••• \(snapshot.accountID.suffix(4))"
        )
        HStack(spacing: QuantXTheme.Spacing.small) {
          QuantXMetricTile(title: "全部", value: snapshot.plans.count.formatted())
          QuantXMetricTile(title: "LIVE", value: live.count.formatted())
          QuantXMetricTile(title: "待授权", value: awaiting.count.formatted())
        }
        Text("规则语义：\(snapshot.capabilities.ruleSemantics)")
          .font(.caption)
          .foregroundStyle(QuantXTheme.secondaryText)
          .fixedSize(horizontal: false, vertical: true)
      }
    }
  }
}

private struct ExitPlanRow: View {
  @Environment(\.dynamicTypeSize) private var dynamicTypeSize
  let plan: ExitPlanItem

  var body: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        HStack(alignment: .top, spacing: QuantXTheme.Spacing.medium) {
          VStack(alignment: .leading, spacing: 3) {
            Text(plan.instrumentCode)
              .font(.headline.monospaced())
            Text("\(plan.bucket) · \(plan.sourceType)")
              .font(.caption)
              .foregroundStyle(QuantXTheme.secondaryText)
          }
          Spacer(minLength: 8)
          if dynamicTypeSize.isAccessibilitySize {
            VStack(alignment: .trailing, spacing: 6) { badges }
          } else {
            HStack(spacing: 6) { badges }
          }
        }

        ProgressView(value: plan.progressFraction)
          .tint(QuantXTheme.accent)
          .accessibilityLabel("退出进度")
          .accessibilityValue("已退出 \(plan.exitedVolume) 股，共保护 \(plan.protectedVolume) 股")

        HStack(spacing: QuantXTheme.Spacing.small) {
          QuantXMetricTile(title: "保护", value: "\(plan.protectedVolume) 股")
          QuantXMetricTile(title: "已退出", value: "\(plan.exitedVolume) 股")
          QuantXMetricTile(title: "剩余", value: "\(plan.remainingVolume) 股")
        }

        HStack {
          Label(plan.authorizationState.title, systemImage: authorizationImage)
            .font(.caption.weight(.semibold))
            .foregroundStyle(authorizationColor)
          Spacer()
          Image(systemName: "chevron.right")
            .font(.caption.weight(.bold))
            .foregroundStyle(.tertiary)
            .accessibilityHidden(true)
        }
      }
    }
    .accessibilityElement(children: .combine)
  }

  @ViewBuilder
  private var badges: some View {
    StatusBadge(
      title: plan.status.title,
      systemImage: statusImage,
      color: statusColor
    )
    StatusBadge(
      title: plan.executionMode.title,
      systemImage: plan.executionMode == .live ? "bolt.shield.fill" : "doc.text.fill",
      color: plan.executionMode == .live ? QuantXTheme.warning : QuantXTheme.accent
    )
  }

  private var statusColor: Color {
    switch plan.status {
    case .active: QuantXTheme.online
    case .paused: QuantXTheme.warning
    case .completed: QuantXTheme.accent
    case .cancelled, .unknown: QuantXTheme.secondaryText
    }
  }

  private var statusImage: String {
    switch plan.status {
    case .active: "waveform.path.ecg"
    case .paused: "pause.circle.fill"
    case .completed: "checkmark.circle.fill"
    case .cancelled: "xmark.circle.fill"
    case .unknown: "questionmark.circle.fill"
    }
  }

  private var authorizationColor: Color {
    if case .authorized = plan.authorizationState { return QuantXTheme.online }
    if case .notApplicable = plan.authorizationState { return QuantXTheme.secondaryText }
    return QuantXTheme.warning
  }

  private var authorizationImage: String {
    if case .authorized = plan.authorizationState { return "checkmark.shield.fill" }
    if case .notApplicable = plan.authorizationState { return "doc.text" }
    return "exclamationmark.shield.fill"
  }
}

private struct ExitPlanDetailView: View {
  @Environment(\.scenePhase) private var scenePhase
  @ObservedObject var store: ExitPlanWorkspace
  let initialPlan: ExitPlanItem

  @State private var operationError: String?

  private var displayedPlan: ExitPlanItem {
    if let snapshot = store.detailState.snapshot, snapshot.plan.id == initialPlan.id {
      return snapshot.plan
    }
    return initialPlan
  }

  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: QuantXTheme.Spacing.large) {
        authorizationBanner(displayedPlan)
        if let success = store.successMessage {
          QuantXStatusBanner(title: "授权已由服务端确认", message: success, status: .ready)
        }
        if let operationError {
          QuantXStatusBanner(title: "操作未完成", message: operationError, status: .blocked)
        }
        detailContent
      }
      .padding(QuantXTheme.Spacing.large)
    }
    .background(QuantXTheme.canvasBackground)
    .navigationTitle(displayedPlan.instrumentCode)
    .navigationBarTitleDisplayMode(.inline)
    .refreshable {
      await store.refresh()
      await store.select(displayedPlan)
    }
    .task { await store.select(initialPlan) }
    .onDisappear { store.clearSelection(planID: initialPlan.id) }
    .onChange(of: scenePhase) { _, phase in
      if phase == .background {
        store.invalidateAuthorizationContext()
        operationError = nil
      }
    }
    .sheet(item: authorizationBinding) { review in
      ExitPlanAuthorizationSheet(
        store: store,
        review: review,
        operationError: $operationError
      )
      .interactiveDismissDisabled(store.operationInProgress)
    }
  }

  @ViewBuilder
  private var detailContent: some View {
    switch store.detailState {
    case .idle, .loading:
      QuantXCard {
        HStack(spacing: QuantXTheme.Spacing.medium) {
          ProgressView()
          Text("正在读取计划规则、容量和审计事件…")
            .foregroundStyle(QuantXTheme.secondaryText)
        }
        .frame(minHeight: 64)
      }
    case .failed(let planID, let message) where planID == initialPlan.id:
      ContentUnavailableView {
        Label("计划详情不可用", systemImage: "exclamationmark.arrow.triangle.2.circlepath")
      } description: {
        Text(message)
      } actions: {
        Button("重试") { Task { await store.retryDetail() } }
          .buttonStyle(.borderedProminent)
      }
    case .loaded(let snapshot, let warning) where snapshot.plan.id == initialPlan.id:
      if let warning {
        QuantXStatusBanner(title: "详情刷新未完成", message: warning, status: .attention)
      }
      identityCard(snapshot.plan)
      progressCard(snapshot.plan)
      rulesCard(snapshot.plan)
      capacityCard(snapshot)
      runtimeCard(snapshot.plan)
      eventsCard(snapshot.events)
      authorizationAction(snapshot.plan)
    default:
      QuantXStatusBanner(
        title: "计划上下文已变化",
        message: "请返回列表并基于服务端最新版本重新进入。",
        status: .blocked
      )
    }
  }

  private func authorizationBanner(_ plan: ExitPlanItem) -> some View {
    switch plan.authorizationState {
    case .authorized(let expiresAt):
      QuantXStatusBanner(
        title: "精确自动授权有效",
        message: "仅绑定计划 v\(plan.configVersion)，有效至 \(dateTime(expiresAt))；每次触发仍会重新经过实时风控。",
        status: .ready
      )
    case .notApplicable:
      QuantXStatusBanner(
        title: "PAPER 计划",
        message: "不会进入实盘自动退出。当前 iOS 专用配置写契约未开放，本页保持只读。",
        status: .unavailable
      )
    case .expired(let expiredAt):
      QuantXStatusBanner(
        title: "自动授权已到期",
        message:
          "未授权的 LIVE SELL 会进入 AWAITING_APPROVAL，等待逐次人工确认。\(expiredAt.map { "上次到期：\(dateTime($0))。" } ?? "")",
        status: .attention
      )
    case .staleVersion(let version):
      QuantXStatusBanner(
        title: "授权版本已失效",
        message:
          "计划当前为 v\(plan.configVersion)，旧授权为 \(version.map { "v\($0)" } ?? "未知版本")；SELL 将进入 AWAITING_APPROVAL。",
        status: .attention
      )
    case .notAuthorized:
      QuantXStatusBanner(
        title: "自动交易未授权",
        message: "计划仍可监控，但 LIVE SELL 会进入 AWAITING_APPROVAL；授权前不会自动越过手机在线确认。",
        status: .attention
      )
    case .unknownMode:
      QuantXStatusBanner(
        title: "未知执行模式",
        message: "客户端不推断新枚举含义，自动授权入口已关闭。",
        status: .blocked
      )
    }
  }

  private func identityCard(_ plan: ExitPlanItem) -> some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(title: "计划身份", subtitle: "服务端配置 v\(plan.configVersion)")
        ExitPlanKeyValueRow(label: "账户", value: maskedAccount(plan.accountID))
        ExitPlanKeyValueRow(label: "计划 ID", value: plan.id, monospaced: true)
        ExitPlanKeyValueRow(label: "标的", value: plan.instrumentCode, monospaced: true)
        ExitPlanKeyValueRow(label: "仓位桶", value: plan.bucket)
        ExitPlanKeyValueRow(label: "来源", value: plan.sourceType)
        ExitPlanKeyValueRow(label: "状态", value: plan.status.title)
        ExitPlanKeyValueRow(label: "执行模式", value: plan.executionMode.title)
        if case .unknown = plan.status {
          Label("服务端返回未知状态；客户端只展示，不推断可操作性。", systemImage: "questionmark.diamond.fill")
            .font(.caption)
            .foregroundStyle(QuantXTheme.warning)
        }
      }
    }
  }

  private func progressCard(_ plan: ExitPlanItem) -> some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(title: "保护进度", subtitle: plan.completionStrategy ?? "服务端未返回完成策略")
        HStack(spacing: QuantXTheme.Spacing.small) {
          QuantXMetricTile(title: "保护", value: "\(plan.protectedVolume) 股")
          QuantXMetricTile(title: "已退出", value: "\(plan.exitedVolume) 股")
          QuantXMetricTile(title: "剩余", value: "\(plan.remainingVolume) 股")
        }
        ProgressView(value: plan.progressFraction)
          .tint(QuantXTheme.accent)
          .accessibilityLabel("退出进度")
          .accessibilityValue("已退出 \(plan.exitedVolume) 股，共保护 \(plan.protectedVolume) 股")
        if let note = plan.completionNote {
          Text(note)
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        }
      }
    }
  }

  private func rulesCard(_ plan: ExitPlanItem) -> some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(title: "精确规则", subtitle: "授权会绑定当前完整 JSON 语义")
        ExitPlanStructuredValueView(value: plan.rules)
        Divider()
        ExitPlanKeyValueRow(
          label: "入场均价",
          value: plan.entryAveragePrice.formatted(
            .number.precision(.fractionLength(2...4))
          )
        )
        ExitPlanKeyValueRow(
          label: "峰值价格",
          value: plan.peakPrice.formatted(.number.precision(.fractionLength(2...4)))
        )
        ExitPlanKeyValueRow(
          label: "峰值回撤",
          value: "\(plan.peakDrawdownPercent.formatted(.number.precision(.fractionLength(0...2))))%"
        )
        if let floor = plan.trailingFloorPercent {
          ExitPlanKeyValueRow(
            label: "移动保护线",
            value: "\(floor.formatted(.number.precision(.fractionLength(0...2))))%"
          )
        }
      }
    }
  }

  private func capacityCard(_ snapshot: ExitPlanDetailSnapshot) -> some View {
    let capacity = snapshot.capacity
    return QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(title: "持仓容量与冲突", subtitle: "\(capacity.instrumentCode) 服务端快照")
        HStack(spacing: QuantXTheme.Spacing.small) {
          QuantXMetricTile(title: "总持仓", value: "\(capacity.totalVolume) 股")
          QuantXMetricTile(title: "当前可卖", value: "\(capacity.availableVolume) 股")
          QuantXMetricTile(title: "冻结", value: "\(capacity.frozenVolume) 股")
        }
        HStack(spacing: QuantXTheme.Spacing.small) {
          QuantXMetricTile(title: "已保护", value: "\(capacity.protectedVolume) 股")
          QuantXMetricTile(title: "待成交 SELL", value: "\(capacity.pendingVolume) 股")
          QuantXMetricTile(title: "未分配", value: "\(capacity.unallocatedVolume) 股")
        }
        QuantXStatusBanner(
          title: "T+1 以授权预览为准",
          message:
            "公开详情未单列 T+1 数量；LIVE 授权预览会展示并签名 total / available / frozen / yesterday / T+1 快照，客户端不会本地猜算。",
          status: .attention
        )
        if capacity.conflicts.isEmpty {
          Label("当前容量快照没有其他退出计划冲突", systemImage: "checkmark.circle.fill")
            .font(.caption)
            .foregroundStyle(QuantXTheme.online)
        } else {
          ForEach(capacity.conflicts) { conflict in
            VStack(alignment: .leading, spacing: 3) {
              Text(conflict.planID)
                .font(.caption.monospaced().weight(.semibold))
              Text(
                "\(conflict.sourceType) · \(conflict.status.title) · 剩余 \(conflict.remainingVolume) 股\(conflict.pending ? " · 有待成交 SELL" : "")"
              )
              .font(.caption2)
              .foregroundStyle(QuantXTheme.secondaryText)
            }
            .accessibilityElement(children: .combine)
          }
        }
      }
    }
  }

  private func runtimeCard(_ plan: ExitPlanItem) -> some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(title: "运行事实", subtitle: plan.dataQuality)
        ExitPlanKeyValueRow(label: "阶段", value: plan.phase)
        if let evaluated = plan.lastEvaluatedAt {
          ExitPlanKeyValueRow(label: "最近评估", value: dateTime(evaluated))
        }
        if let decision = plan.lastDecision {
          ExitPlanKeyValueRow(label: "最近决策", value: decision)
        }
        if let intentID = plan.pendingIntentID {
          ExitPlanKeyValueRow(label: "待确认意图", value: intentID, monospaced: true)
        }
        if let orderID = plan.pendingClientOrderID {
          ExitPlanKeyValueRow(label: "待成交订单", value: orderID, monospaced: true)
        }
        if let error = plan.lastError {
          QuantXStatusBanner(title: "最近错误", message: error, status: .blocked)
        }
      }
    }
  }

  private func eventsCard(_ events: [ExitPlanEvent]) -> some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(title: "审计时间线", subtitle: "最近 \(events.count) 条")
        if events.isEmpty {
          Text("服务端没有返回审计事件。")
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        } else {
          ForEach(events.prefix(20)) { event in
            HStack(alignment: .top, spacing: QuantXTheme.Spacing.medium) {
              Circle()
                .fill(QuantXTheme.accent)
                .frame(width: 8, height: 8)
                .padding(.top, 5)
                .accessibilityHidden(true)
              VStack(alignment: .leading, spacing: 3) {
                Text(event.type)
                  .font(.caption.weight(.semibold))
                Text(dateTime(event.createdAt))
                  .font(.caption2.monospacedDigit())
                  .foregroundStyle(QuantXTheme.secondaryText)
                Text(event.payload.summary)
                  .font(.caption2)
                  .foregroundStyle(QuantXTheme.secondaryText)
                  .lineLimit(3)
              }
            }
            .accessibilityElement(children: .combine)
          }
          if events.count > 20 {
            Text("为保持移动端可读性，仅展示最近 20 条；完整记录仍保留在服务端。")
              .font(.caption2)
              .foregroundStyle(QuantXTheme.secondaryText)
          }
        }
      }
    }
  }

  @ViewBuilder
  private func authorizationAction(_ plan: ExitPlanItem) -> some View {
    if plan.executionMode == .live {
      QuantXCard {
        VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
          SectionTitle(title: "自动实盘退出授权", subtitle: "预览不会创建委托")
          if let reason = store.authorizationUnavailableReason(for: plan) {
            QuantXStatusBanner(title: "授权入口不可用", message: reason, status: .blocked)
          }
          Button {
            Task { await requestAuthorizationPreview(plan) }
          } label: {
            if store.operationInProgress {
              ProgressView().frame(maxWidth: .infinity)
            } else {
              Label("获取精确授权预览", systemImage: "doc.text.magnifyingglass")
                .frame(maxWidth: .infinity)
            }
          }
          .buttonStyle(.borderedProminent)
          .controlSize(.large)
          .tint(QuantXTheme.approvalAction)
          .disabled(
            store.operationInProgress
              || store.authorizationUnavailableReason(for: plan) != nil
          )
          .accessibilityHint("只获取一次性挑战；下一步仍需核对并使用生物识别")
        }
      }
    }
  }

  private var authorizationBinding: Binding<ExitPlanAuthorizationReview?> {
    Binding(
      get: { store.pendingAuthorization },
      set: { value in
        if value == nil { store.dismissAuthorizationReview() }
      }
    )
  }

  private func requestAuthorizationPreview(_ plan: ExitPlanItem) async {
    operationError = nil
    do {
      try await store.previewAuthorization(for: plan)
    } catch is CancellationError {
      return
    } catch {
      operationError = error.localizedDescription
    }
  }

  private func maskedAccount(_ accountID: String) -> String {
    accountID.count > 4 ? "•••• \(accountID.suffix(4))" : accountID
  }

  private func dateTime(_ value: Date) -> String {
    value.formatted(date: .abbreviated, time: .shortened)
  }
}

private struct ExitPlanAuthorizationSheet: View {
  @Environment(\.dismiss) private var dismiss
  @ObservedObject var store: ExitPlanWorkspace
  let review: ExitPlanAuthorizationReview
  @Binding var operationError: String?

  @State private var localError: String?

  var body: some View {
    NavigationStack {
      ScrollView {
        LazyVStack(alignment: .leading, spacing: QuantXTheme.Spacing.large) {
          QuantXStatusBanner(
            title: "这不是卖出委托",
            message: "确认只授权这一计划版本在有效期内自动触发；每个 SELL 仍重新经过实时风控、实盘门禁和 QMT 回报收敛。",
            status: .attention
          )
          bindingCard
          positionCard
          rulesCard
          readinessCard
          conflictsCard
          warningsCard
          if let localError {
            QuantXStatusBanner(title: "授权未完成", message: localError, status: .blocked)
          }
        }
        .padding(QuantXTheme.Spacing.large)
      }
      .background(QuantXTheme.canvasBackground)
      .navigationTitle("核对自动退出授权")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .cancellationAction) {
          Button("取消") {
            store.dismissAuthorizationReview()
            dismiss()
          }
          .disabled(store.operationInProgress)
        }
      }
      .safeAreaInset(edge: .bottom) { confirmationBar }
    }
  }

  private var bindingCard: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(title: "精确绑定", subtitle: "挑战 \(shortID(review.id))")
        ExitPlanKeyValueRow(label: "账户", value: maskedAccount(review.accountID))
        ExitPlanKeyValueRow(label: "标的", value: review.instrumentCode, monospaced: true)
        ExitPlanKeyValueRow(label: "计划 ID", value: review.planID, monospaced: true)
        ExitPlanKeyValueRow(label: "计划版本", value: "v\(review.configVersion)")
        ExitPlanKeyValueRow(label: "执行模式", value: review.executionMode.title)
        ExitPlanKeyValueRow(label: "仓位桶", value: review.bucket)
        ExitPlanKeyValueRow(label: "来源", value: review.sourceType)
        ExitPlanKeyValueRow(label: "保护数量", value: "\(review.protectedVolume) 股")
        ExitPlanKeyValueRow(label: "已退出", value: "\(review.exitedVolume) 股")
        ExitPlanKeyValueRow(label: "剩余", value: "\(review.remainingVolume) 股")
        ExitPlanKeyValueRow(
          label: "授权有效至",
          value: dateTime(review.authorizationExpiresAt)
        )
        ExitPlanKeyValueRow(
          label: "本次挑战到期",
          value: dateTime(review.challengeExpiresAt)
        )
        ExitPlanKeyValueRow(
          label: "安全指纹",
          value: shortID(review.authorizationFingerprint),
          monospaced: true
        )
      }
    }
  }

  private var positionCard: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(title: "持仓与 T+1 快照", subtitle: review.t1Policy)
        HStack(spacing: QuantXTheme.Spacing.small) {
          QuantXMetricTile(title: "总持仓", value: "\(review.position.totalVolume) 股")
          QuantXMetricTile(title: "可卖", value: "\(review.position.availableVolume) 股")
          QuantXMetricTile(title: "T+1 不可卖", value: "\(review.position.t1UnavailableVolume) 股")
        }
        HStack(spacing: QuantXTheme.Spacing.small) {
          QuantXMetricTile(title: "冻结", value: "\(review.position.frozenVolume) 股")
          QuantXMetricTile(title: "昨仓", value: "\(review.position.yesterdayVolume) 股")
        }
        if let updatedAt = review.position.updatedAt {
          Text("持仓更新时间：\(dateTime(updatedAt))")
            .font(.caption.monospacedDigit())
            .foregroundStyle(QuantXTheme.secondaryText)
        } else {
          Text("服务端未返回持仓更新时间；若门禁不满足，确认会失败。")
            .font(.caption)
            .foregroundStyle(QuantXTheme.warning)
        }
      }
    }
  }

  private var rulesCard: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(title: "触发规则", subtitle: "修改任一字段都会使授权失效")
        ExitPlanStructuredValueView(value: review.rules)
        Divider()
        SectionTitle(title: "委托策略", subtitle: "服务端签名的执行策略")
        ExitPlanStructuredValueView(value: review.executionPolicy)
      }
    }
  }

  private var readinessCard: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(title: "实盘就绪检查", subtitle: "确认时服务端会再次校验")
        ExitPlanStructuredValueView(value: review.readiness)
      }
    }
  }

  @ViewBuilder
  private var conflictsCard: some View {
    if !review.otherProtections.isEmpty {
      QuantXCard {
        VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
          SectionTitle(
            title: "其他保护与冲突",
            subtitle: "共 \(review.otherProtections.count) 个"
          )
          ForEach(review.otherProtections) { conflict in
            VStack(alignment: .leading, spacing: 4) {
              Text(conflict.planID)
                .font(.caption.monospaced().weight(.semibold))
              Text(
                "\(conflict.sourceType) · \(conflict.status.title) · v\(conflict.configVersion) · 剩余 \(conflict.remainingVolume) 股"
              )
              .font(.caption2)
              .foregroundStyle(QuantXTheme.secondaryText)
              if conflict.pending {
                Label("存在待成交 SELL", systemImage: "clock.badge.exclamationmark")
                  .font(.caption2.weight(.semibold))
                  .foregroundStyle(QuantXTheme.warning)
              }
            }
            .accessibilityElement(children: .combine)
          }
        }
      }
    }
  }

  private var warningsCard: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(title: "确认前检查", subtitle: "服务端风险提示")
        ForEach(Array(review.warnings.enumerated()), id: \.offset) { _, warning in
          Label(warning, systemImage: "exclamationmark.triangle.fill")
            .font(.caption)
            .foregroundStyle(QuantXTheme.warning)
            .fixedSize(horizontal: false, vertical: true)
        }
      }
    }
  }

  private var confirmationBar: some View {
    TimelineView(.periodic(from: .now, by: 1)) { context in
      let expired = review.isChallengeExpired(at: context.date)
      VStack(spacing: 7) {
        Button {
          Task { await confirm() }
        } label: {
          if store.operationInProgress {
            ProgressView().frame(maxWidth: .infinity)
          } else {
            Label("使用 Face ID / Touch ID 授权", systemImage: "faceid")
              .frame(maxWidth: .infinity)
          }
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .tint(QuantXTheme.approvalAction)
        .disabled(store.operationInProgress || expired)
        .accessibilityHint("本次生物识别仅确认当前页面列出的精确计划版本")

        Text(
          expired
            ? "挑战已过期，请关闭后重新预览。"
            : "确认请求返回前不会显示授权成功；网络结果不确定时会刷新服务端真源。"
        )
        .font(.caption2)
        .foregroundStyle(expired ? QuantXTheme.warning : QuantXTheme.secondaryText)
      }
      .padding(.horizontal, QuantXTheme.Spacing.large)
      .padding(.vertical, QuantXTheme.Spacing.medium)
      .background(.ultraThinMaterial)
    }
  }

  private func confirm() async {
    localError = nil
    operationError = nil
    do {
      try await store.confirmPendingAuthorization()
      dismiss()
    } catch is CancellationError {
      return
    } catch {
      localError = error.localizedDescription
      operationError = error.localizedDescription
      if store.pendingAuthorization == nil { dismiss() }
    }
  }

  private func maskedAccount(_ accountID: String) -> String {
    accountID.count > 4 ? "•••• \(accountID.suffix(4))" : accountID
  }

  private func shortID(_ value: String) -> String {
    guard value.count > 16 else { return value }
    return "\(value.prefix(8))…\(value.suffix(6))"
  }

  private func dateTime(_ value: Date) -> String {
    value.formatted(date: .abbreviated, time: .shortened)
  }
}

private struct ExitPlanStructuredValueView: View {
  let value: ExitPlanStructuredValue

  var body: some View {
    VStack(alignment: .leading, spacing: QuantXTheme.Spacing.small) {
      ForEach(value.topLevelFields.prefix(24)) { field in
        ExitPlanKeyValueRow(label: humanized(field.key), value: field.value.summary)
      }
      if value.topLevelFields.count > 24 {
        Text("其余字段已折叠；授权仍绑定完整结构。")
          .font(.caption2)
          .foregroundStyle(QuantXTheme.secondaryText)
      }
    }
  }

  private func humanized(_ key: String) -> String {
    let replacements = [
      "rule_type": "规则类型",
      "target_price": "目标价",
      "return_pct": "收益率",
      "drawdown_pct": "回撤比例",
      "sell_mode": "数量策略",
      "sell_ratio_pct": "卖出比例",
      "sell_volume": "卖出数量",
      "execution_mode": "委托模式",
      "price_type": "报价类型",
      "snapshot_id": "快照 ID",
      "snapshot_hash": "快照哈希",
      "protocol_version": "Agent 协议",
      "reconcile_status": "对账状态",
      "kill_switch": "Kill Switch",
    ]
    return replacements[key] ?? key.replacingOccurrences(of: "_", with: " ")
  }
}

private struct ExitPlanKeyValueRow: View {
  let label: String
  let value: String
  var monospaced = false

  var body: some View {
    ViewThatFits(in: .horizontal) {
      HStack(alignment: .firstTextBaseline, spacing: QuantXTheme.Spacing.medium) {
        title
        Spacer(minLength: 12)
        content.multilineTextAlignment(.trailing)
      }
      VStack(alignment: .leading, spacing: 3) {
        title
        content
      }
    }
    .accessibilityElement(children: .combine)
  }

  private var title: some View {
    Text(label)
      .font(.caption)
      .foregroundStyle(QuantXTheme.secondaryText)
  }

  @ViewBuilder
  private var content: some View {
    if monospaced {
      Text(value)
        .font(.caption.monospaced())
        .textSelection(.enabled)
    } else {
      Text(value)
        .font(.caption.weight(.medium))
        .fixedSize(horizontal: false, vertical: true)
    }
  }
}
