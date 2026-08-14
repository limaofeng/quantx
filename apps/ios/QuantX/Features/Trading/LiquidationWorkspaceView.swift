import SwiftUI

struct LiquidationWorkspaceView: View {
  private enum Section: String, CaseIterable, Identifiable {
    case plans
    case holdings

    var id: Self { self }

    var title: String {
      switch self {
      case .plans: "退出计划"
      case .holdings: "持仓清仓"
      }
    }
  }

  @EnvironmentObject private var model: AppModel
  @State private var section = Section.plans

  var body: some View {
    VStack(spacing: 0) {
      Picker("卖出管理分区", selection: $section) {
        ForEach(Section.allCases) { section in
          Text(section.title).tag(section)
        }
      }
      .pickerStyle(.segmented)
      .padding(.horizontal, QuantXTheme.Spacing.large)
      .padding(.top, QuantXTheme.Spacing.small)
      .padding(.bottom, QuantXTheme.Spacing.xSmall)
      .background(QuantXTheme.canvasBackground)

      switch section {
      case .plans:
        ExitPlanWorkspaceView(store: model.exitPlanWorkspace)
      case .holdings:
        LiquidationSelectionWorkspace(
          store: model.liquidationStore,
          accountID: model.primaryTradingAccountID,
          positions: model.portfolioState.snapshot?.positions.filter { $0.volume > 0 } ?? [],
          refreshPortfolio: { await model.refreshPortfolio() }
        )
      }
    }
    .navigationTitle("卖出管理")
    .navigationBarTitleDisplayMode(.inline)
  }
}

private struct LiquidationSelectionWorkspace: View {
  @Environment(\.scenePhase) private var scenePhase
  @ObservedObject var store: LiquidationStore

  let accountID: String?
  let positions: [PortfolioPosition]
  let refreshPortfolio: @MainActor () async -> Void

  @State private var scope = LiquidationScope.single
  @State private var selectedCodes: Set<String> = []
  @State private var completionStrategy = LiquidationCompletionStrategy.availableNow
  @State private var conflictStrategy = LiquidationConflictStrategy.unallocatedOnly
  @State private var executionMode = LiquidationExecutionMode.paper
  @State private var preview: LiquidationPreviewTicket?
  @State private var previewError: String?

  private var sortedPositions: [PortfolioPosition] {
    positions.sorted {
      $0.stockCode.localizedStandardCompare($1.stockCode) == .orderedAscending
    }
  }

  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.large) {
        QuantXStatusBanner(
          title: "卖出管理",
          message: "先固定服务器持仓快照，再用一次性挑战创建退出计划；不会直接宣称已报单或成交。",
          status: .attention
        )
        accountCard
        scopeCard
        holdingSelection
        LiquidationStrategyControls(
          completionStrategy: $completionStrategy,
          conflictStrategy: $conflictStrategy,
          executionMode: $executionMode
        )
        capabilityStatus
        if let previewError {
          QuantXStatusBanner(
            title: "无法获取清仓预览",
            message: previewError,
            status: .blocked
          )
        }
        allPositionsEntry
      }
      .padding(QuantXTheme.Spacing.large)
    }
    .background(QuantXTheme.canvasBackground)
    .navigationTitle("卖出管理")
    .navigationBarTitleDisplayMode(.inline)
    .safeAreaInset(edge: .bottom) {
      previewActionBar
    }
    .sheet(item: $preview) { preview in
      LiquidationConfirmationSheet(store: store, preview: preview)
    }
    .task {
      normalizeSelection()
    }
    .onChange(of: positions.map(\.stockCode)) { _, _ in
      normalizeSelection()
      previewError = nil
    }
    .onChange(of: scope) { _, _ in
      normalizeSelection()
      clearTransientState()
    }
    .onChange(of: completionStrategy) { _, _ in clearTransientState() }
    .onChange(of: conflictStrategy) { _, _ in clearTransientState() }
    .onChange(of: executionMode) { _, _ in clearTransientState() }
    .onChange(of: accountID) { _, _ in clearSensitivePreview() }
    .onChange(of: store.challengeContextID) { _, _ in clearSensitivePreview() }
    .onChange(of: scenePhase) { _, phase in
      if phase == .background { clearSensitivePreview() }
    }
    .onDisappear {
      clearSensitivePreview()
    }
  }

  private var accountCard: some View {
    QuantXCard {
      HStack(alignment: .top, spacing: QuantXTheme.Spacing.medium) {
        Image(systemName: "person.crop.circle.badge.checkmark")
          .font(.title2)
          .foregroundStyle(accountID == nil ? QuantXTheme.warning : QuantXTheme.accent)
          .accessibilityHidden(true)
        VStack(alignment: .leading, spacing: 4) {
          Text("当前主账户")
            .font(.headline)
          Text(accountID.map(maskedAccount) ?? "主账户持仓尚未完成同步")
            .font(.subheadline.monospaced())
            .foregroundStyle(accountID == nil ? QuantXTheme.warning : QuantXTheme.secondaryText)
        }
        Spacer(minLength: 8)
        Button("刷新") {
          Task { await refreshPortfolio() }
        }
        .buttonStyle(.bordered)
        .frame(minHeight: 44)
        .accessibilityHint("重新读取当前主账户和持仓")
      }
    }
  }

  private var scopeCard: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(title: "卖出范围", subtitle: "主入口不提供全部持仓")
        Picker("卖出范围", selection: $scope) {
          Text(LiquidationScope.single.title).tag(LiquidationScope.single)
          Text(LiquidationScope.selected.title).tag(LiquidationScope.selected)
        }
        .pickerStyle(.segmented)
        Text(
          scope == .single
            ? "一次只核对一只持仓。"
            : "只处理你明确勾选的证券集合。"
        )
        .font(.caption)
        .foregroundStyle(QuantXTheme.secondaryText)
      }
    }
  }

  @ViewBuilder
  private var holdingSelection: some View {
    if sortedPositions.isEmpty {
      ContentUnavailableView {
        Label("没有可选择的持仓", systemImage: "tray")
      } description: {
        Text("请刷新主账户持仓；运行时不会使用模拟持仓生成清仓预览。")
      } actions: {
        Button("刷新持仓") {
          Task { await refreshPortfolio() }
        }
        .buttonStyle(.borderedProminent)
      }
    } else {
      QuantXCard {
        VStack(alignment: .leading, spacing: QuantXTheme.Spacing.small) {
          SectionTitle(
            title: scope == .single ? "选择一只持仓" : "选择持仓",
            subtitle: selectionSummary
          )
          ForEach(sortedPositions) { position in
            LiquidationHoldingSelectionRow(
              position: position,
              selected: selectedCodes.contains(position.stockCode),
              singleSelection: scope == .single,
              action: { toggle(position.stockCode) }
            )
          }
        }
      }
    }
  }

  @ViewBuilder
  private var capabilityStatus: some View {
    if let reason = store.previewUnavailableReason(for: executionMode) {
      QuantXStatusBanner(
        title: "卖出管理不可用",
        message: reason,
        status: .blocked
      )
    } else if let reason = store.confirmationUnavailableReason(for: executionMode) {
      QuantXStatusBanner(
        title: "当前仅可查看预览",
        message: "\(reason)。预览不会创建任何计划。",
        status: .attention
      )
    } else if executionMode == .live {
      QuantXStatusBanner(
        title: "已显式选择实盘",
        message: "确认时仍会单独要求 Face ID / Touch ID，服务端会重新校验实盘开关、对账、Agent 与 Kill Switch。",
        status: .attention
      )
    }
  }

  private var allPositionsEntry: some View {
    NavigationLink {
      LiquidationAllPositionsView(
        store: store,
        accountID: accountID,
        positions: sortedPositions
      )
    } label: {
      QuantXCard {
        HStack(alignment: .top, spacing: QuantXTheme.Spacing.medium) {
          Image(systemName: "exclamationmark.shield.fill")
            .font(.title2)
            .foregroundStyle(QuantXTheme.warning)
            .frame(width: 42, height: 42)
            .background(
              QuantXTheme.warning.opacity(0.12),
              in: RoundedRectangle(cornerRadius: 12)
            )
            .accessibilityHidden(true)
          VStack(alignment: .leading, spacing: 5) {
            Text("全部持仓（高风险入口）")
              .font(.headline)
              .foregroundStyle(.primary)
            Text("进入二级页面后重新核对全部证券与执行策略")
              .font(.subheadline)
              .foregroundStyle(QuantXTheme.secondaryText)
          }
          Spacer(minLength: 8)
          Image(systemName: "chevron.right")
            .foregroundStyle(.tertiary)
            .accessibilityHidden(true)
        }
      }
    }
    .buttonStyle(.plain)
    .accessibilityHint("进入全部持仓清仓的二级高风险确认入口")
  }

  private var previewActionBar: some View {
    VStack(spacing: 7) {
      Button {
        Task { await requestPreview() }
      } label: {
        if store.operationInProgress {
          ProgressView()
            .frame(maxWidth: .infinity)
        } else {
          Label("获取服务器清仓预览", systemImage: "doc.text.magnifyingglass")
            .frame(maxWidth: .infinity)
        }
      }
      .buttonStyle(.borderedProminent)
      .controlSize(.large)
      .tint(QuantXTheme.approvalAction)
      .disabled(
        store.operationInProgress
          || selectedCodes.isEmpty
          || store.previewUnavailableReason(for: executionMode) != nil
      )
      .accessibilityHint("只固定服务器快照并获取一次性挑战，不创建退出计划")

      Text("默认 PAPER；只有你显式改为 LIVE 才会请求实盘预览。")
        .font(.caption2)
        .foregroundStyle(QuantXTheme.secondaryText)
    }
    .padding(.horizontal, QuantXTheme.Spacing.large)
    .padding(.vertical, QuantXTheme.Spacing.medium)
    .background(.ultraThinMaterial)
  }

  private var selectionSummary: String {
    scope == .single ? "必须恰好 1 只" : "已选 \(selectedCodes.count) 只"
  }

  private func toggle(_ stockCode: String) {
    if scope == .single {
      selectedCodes = [stockCode]
    } else if selectedCodes.contains(stockCode) {
      selectedCodes.remove(stockCode)
    } else {
      selectedCodes.insert(stockCode)
    }
    clearTransientState()
  }

  private func normalizeSelection() {
    let available = Set(sortedPositions.map(\.stockCode))
    selectedCodes.formIntersection(available)
    if scope == .single, selectedCodes.count != 1 {
      selectedCodes = []
    }
  }

  private func requestPreview() async {
    previewError = nil
    do {
      preview = try await store.preview(
        scope: scope,
        instrumentCodes: selectedCodes.sorted(),
        completionStrategy: completionStrategy,
        conflictStrategy: conflictStrategy,
        executionMode: executionMode
      )
    } catch is CancellationError {
      return
    } catch {
      previewError = error.localizedDescription
    }
  }

  private func clearTransientState() {
    preview = nil
    previewError = nil
  }

  private func clearSensitivePreview() {
    preview = nil
    previewError = nil
  }

  private func maskedAccount(_ accountID: String) -> String {
    accountID.count > 4 ? "•••• \(accountID.suffix(4))" : accountID
  }
}

private struct LiquidationHoldingSelectionRow: View {
  @Environment(\.dynamicTypeSize) private var dynamicTypeSize

  let position: PortfolioPosition
  let selected: Bool
  let singleSelection: Bool
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      if dynamicTypeSize.isAccessibilitySize {
        VStack(alignment: .leading, spacing: QuantXTheme.Spacing.small) {
          identity
          HStack {
            Text("持仓 \(position.volume.formatted())")
            Spacer(minLength: 8)
            Text("可卖 \(position.availableVolume.formatted())")
              .foregroundStyle(QuantXTheme.secondaryText)
          }
          .font(.caption.monospacedDigit())
        }
        .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
        .contentShape(Rectangle())
      } else {
        HStack(spacing: QuantXTheme.Spacing.medium) {
          identity
          Spacer(minLength: 8)
          VStack(alignment: .trailing, spacing: 3) {
            Text("持仓 \(position.volume.formatted())")
            Text("可卖 \(position.availableVolume.formatted())")
              .foregroundStyle(QuantXTheme.secondaryText)
          }
          .font(.caption.monospacedDigit())
        }
        .frame(minHeight: 52)
        .contentShape(Rectangle())
      }
    }
    .buttonStyle(.plain)
    .accessibilityLabel(
      "\(position.displayName)，\(position.stockCode)，持仓 \(position.volume) 股，可卖 \(position.availableVolume) 股"
    )
    .accessibilityValue(selected ? "已选择" : "未选择")
    .accessibilityHint(singleSelection ? "设为唯一清仓证券" : "切换是否纳入选中清仓")
  }

  private var identity: some View {
    HStack(spacing: QuantXTheme.Spacing.medium) {
      Image(systemName: selectionIcon)
        .font(.title3)
        .foregroundStyle(selected ? QuantXTheme.accent : QuantXTheme.secondaryText)
        .accessibilityHidden(true)
      VStack(alignment: .leading, spacing: 3) {
        Text(position.displayName)
          .font(.headline)
          .foregroundStyle(.primary)
        Text(position.stockCode)
          .font(.caption.monospaced())
          .foregroundStyle(QuantXTheme.secondaryText)
      }
      Spacer(minLength: 0)
    }
  }

  private var selectionIcon: String {
    if singleSelection {
      selected ? "largecircle.fill.circle" : "circle"
    } else {
      selected ? "checkmark.square.fill" : "square"
    }
  }
}

struct LiquidationStrategyControls: View {
  @Binding var completionStrategy: LiquidationCompletionStrategy
  @Binding var conflictStrategy: LiquidationConflictStrategy
  @Binding var executionMode: LiquidationExecutionMode

  var body: some View {
    VStack(spacing: QuantXTheme.Spacing.large) {
      QuantXCard {
        VStack(alignment: .leading, spacing: QuantXTheme.Spacing.small) {
          SectionTitle(title: "完成策略", subtitle: "明确选择数量边界")
          ForEach(LiquidationCompletionStrategy.allCases) { strategy in
            LiquidationChoiceRow(
              title: strategy.title,
              detail: strategy.detail,
              selected: completionStrategy == strategy,
              destructive: false,
              action: { completionStrategy = strategy }
            )
          }
        }
      }

      QuantXCard {
        VStack(alignment: .leading, spacing: QuantXTheme.Spacing.small) {
          SectionTitle(title: "冲突策略", subtitle: "默认不替换现有计划")
          ForEach(LiquidationConflictStrategy.allCases) { strategy in
            LiquidationChoiceRow(
              title: strategy.title,
              detail: strategy.detail,
              selected: conflictStrategy == strategy,
              destructive: strategy == .replaceCancellable,
              action: { conflictStrategy = strategy }
            )
          }
        }
      }

      QuantXCard {
        VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
          SectionTitle(title: "执行模式", subtitle: "默认且始终先显示 PAPER")
          Picker("执行模式", selection: $executionMode) {
            ForEach(LiquidationExecutionMode.allCases) { mode in
              Text(mode.title).tag(mode)
            }
          }
          .pickerStyle(.segmented)
          Text(
            executionMode == .paper
              ? "只创建模拟清仓计划，用于先验收完整执行链路。"
              : "实盘不会自动开启；预览和确认均按最新实盘门禁失败关闭。"
          )
          .font(.caption)
          .foregroundStyle(
            executionMode == .paper ? QuantXTheme.secondaryText : QuantXTheme.warning
          )
          .fixedSize(horizontal: false, vertical: true)
        }
      }
    }
  }
}

private struct LiquidationChoiceRow: View {
  let title: String
  let detail: String
  let selected: Bool
  let destructive: Bool
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      HStack(alignment: .top, spacing: QuantXTheme.Spacing.medium) {
        Image(systemName: selected ? "checkmark.circle.fill" : "circle")
          .font(.title3)
          .foregroundStyle(
            selected
              ? (destructive ? QuantXTheme.warning : QuantXTheme.accent)
              : QuantXTheme.secondaryText
          )
          .accessibilityHidden(true)
        VStack(alignment: .leading, spacing: 4) {
          Text(title)
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(destructive ? QuantXTheme.warning : .primary)
          Text(detail)
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
            .fixedSize(horizontal: false, vertical: true)
        }
        Spacer(minLength: 4)
      }
      .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
      .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
    .accessibilityLabel("\(title)，\(detail)")
    .accessibilityValue(selected ? "已选择" : "未选择")
  }
}

private struct LiquidationAllPositionsView: View {
  @Environment(\.scenePhase) private var scenePhase
  @ObservedObject var store: LiquidationStore

  let accountID: String?
  let positions: [PortfolioPosition]

  @State private var completionStrategy = LiquidationCompletionStrategy.availableNow
  @State private var conflictStrategy = LiquidationConflictStrategy.unallocatedOnly
  @State private var executionMode = LiquidationExecutionMode.paper
  @State private var acknowledged = false
  @State private var preview: LiquidationPreviewTicket?
  @State private var previewError: String?

  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.large) {
        QuantXStatusBanner(
          title: "全部持仓高风险入口",
          message: "服务器会在预览时固定当前主账户的完整持仓集合；确认后的新增证券和新增数量不会自动加入。",
          status: .blocked
        )
        allPositionSummary
        LiquidationStrategyControls(
          completionStrategy: $completionStrategy,
          conflictStrategy: $conflictStrategy,
          executionMode: $executionMode
        )
        if let reason = store.previewUnavailableReason(for: executionMode) {
          QuantXStatusBanner(title: "全部持仓预览不可用", message: reason, status: .blocked)
        } else if let reason = store.confirmationUnavailableReason(for: executionMode) {
          QuantXStatusBanner(
            title: "当前仅可查看预览",
            message: "\(reason)。预览不会创建任何计划。",
            status: .attention
          )
        }
        acknowledgement
        if let previewError {
          QuantXStatusBanner(title: "无法获取全部持仓预览", message: previewError, status: .blocked)
        }
      }
      .padding(QuantXTheme.Spacing.large)
    }
    .background(QuantXTheme.canvasBackground)
    .navigationTitle("全部持仓")
    .navigationBarTitleDisplayMode(.inline)
    .safeAreaInset(edge: .bottom) { previewActionBar }
    .sheet(item: $preview) { preview in
      LiquidationConfirmationSheet(store: store, preview: preview)
    }
    .onChange(of: completionStrategy) { _, _ in resetAcknowledgement() }
    .onChange(of: conflictStrategy) { _, _ in resetAcknowledgement() }
    .onChange(of: executionMode) { _, _ in resetAcknowledgement() }
    .onChange(of: accountID) { _, _ in clearSensitivePreview() }
    .onChange(of: store.challengeContextID) { _, _ in clearSensitivePreview() }
    .onChange(of: scenePhase) { _, phase in
      if phase == .background { clearSensitivePreview() }
    }
    .onDisappear { clearSensitivePreview() }
  }

  private var allPositionSummary: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(title: "当前本机快照", subtitle: "服务器预览仍是最终边界")
        HStack {
          Text("持仓证券")
            .foregroundStyle(QuantXTheme.secondaryText)
          Spacer()
          Text("\(positions.count) 只")
            .fontWeight(.semibold)
            .monospacedDigit()
        }
        .font(.subheadline)
        ForEach(positions) { position in
          HStack {
            Text(position.displayName)
            Spacer(minLength: 8)
            Text(position.stockCode)
              .monospaced()
              .foregroundStyle(QuantXTheme.secondaryText)
          }
          .font(.caption)
        }
      }
    }
  }

  private var acknowledgement: some View {
    QuantXCard {
      Toggle(isOn: $acknowledged) {
        VStack(alignment: .leading, spacing: 4) {
          Text("我已核对全部持仓范围")
            .font(.headline)
          Text("这只是允许请求服务器预览；仍不会直接创建计划或下单。")
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        }
      }
      .tint(QuantXTheme.warning)
      .accessibilityHint("开启后才能获取全部持仓服务器预览")
    }
  }

  private var previewActionBar: some View {
    VStack(spacing: 7) {
      Button {
        Task { await requestPreview() }
      } label: {
        if store.operationInProgress {
          ProgressView().frame(maxWidth: .infinity)
        } else {
          Label("预览全部持仓计划", systemImage: "exclamationmark.shield")
            .frame(maxWidth: .infinity)
        }
      }
      .buttonStyle(.borderedProminent)
      .controlSize(.large)
      .tint(QuantXTheme.warning)
      .disabled(
        !acknowledged
          || positions.isEmpty
          || store.operationInProgress
          || store.previewUnavailableReason(for: executionMode) != nil
      )
      .accessibilityHint("请求固定全部持仓快照，不直接创建退出计划")
      Text("ALL 只存在于本二级入口；确认页仍会逐只展示纳入和跳过原因。")
        .font(.caption2)
        .foregroundStyle(QuantXTheme.secondaryText)
    }
    .padding(.horizontal, QuantXTheme.Spacing.large)
    .padding(.vertical, QuantXTheme.Spacing.medium)
    .background(.ultraThinMaterial)
  }

  private func requestPreview() async {
    previewError = nil
    do {
      preview = try await store.preview(
        scope: .all,
        instrumentCodes: [],
        completionStrategy: completionStrategy,
        conflictStrategy: conflictStrategy,
        executionMode: executionMode
      )
    } catch is CancellationError {
      return
    } catch {
      previewError = error.localizedDescription
    }
  }

  private func clearTransientState() {
    preview = nil
    previewError = nil
  }

  private func clearSensitivePreview() {
    preview = nil
    previewError = nil
    acknowledged = false
  }

  private func resetAcknowledgement() {
    clearTransientState()
    acknowledged = false
  }
}
