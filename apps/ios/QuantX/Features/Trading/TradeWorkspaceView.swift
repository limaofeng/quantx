import SwiftUI

struct TradeWorkspaceView: View {
  private enum Route: Hashable {
    case orderTicket(direction: ManualOrderDirection, instrumentCode: String?)
    case activity
    case liquidation
  }

  @EnvironmentObject private var model: AppModel
  @State private var path: [Route] = []

  var body: some View {
    NavigationStack(path: $path) {
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
        case .orderTicket(let direction, let instrumentCode):
          ManualOrderTicketView(
            direction: direction,
            initialInstrumentCode: instrumentCode ?? ""
          )
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
      if case .idle = model.portfolioState {
        await model.refreshPortfolio()
      }
    }
    .onAppear {
      openPendingDraftIfNeeded()
    }
    .onChange(of: model.pendingManualOrderDraft?.id) { _, _ in
      openPendingDraftIfNeeded()
    }
  }

  private var orderEntry: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.large) {
        SectionTitle(title: "手动交易", subtitle: "限价 / 合法最优价")

        HStack(spacing: QuantXTheme.Spacing.medium) {
          NavigationLink(value: Route.orderTicket(direction: .buy, instrumentCode: nil)) {
            Label("买入", systemImage: "arrow.down.circle.fill")
              .font(.headline)
              .frame(maxWidth: .infinity, minHeight: 48)
          }
          .buttonStyle(.borderedProminent)
          .tint(QuantXTheme.positive)
          .accessibilityHint("进入买入票据，不会立即提交订单")

          NavigationLink(value: Route.orderTicket(direction: .sell, instrumentCode: nil)) {
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

  private func openPendingDraftIfNeeded() {
    guard let draft = model.consumePendingManualOrderDraft() else { return }
    path = [
      .orderTicket(
        direction: draft.direction,
        instrumentCode: draft.instrumentCode
      )
    ]
  }
}

private struct ManualOrderTicketView: View {
  @EnvironmentObject private var model: AppModel
  @Environment(\.dynamicTypeSize) private var dynamicTypeSize
  @Environment(\.scenePhase) private var scenePhase

  let direction: ManualOrderDirection

  @State private var instrumentCode: String
  @State private var quoteType = ManualOrderQuoteType.limit
  @State private var volumeText = ""
  @State private var limitPriceText = ""
  @State private var previewInProgress = false
  @State private var previewTicket: ManualOrderPreviewTicket?
  @State private var errorMessage: String?
  @State private var queued = false

  init(direction: ManualOrderDirection, initialInstrumentCode: String) {
    self.direction = direction
    _instrumentCode = State(initialValue: initialInstrumentCode)
  }

  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.large) {
        accountScope
        ticketForm

        if queued {
          QuantXStatusBanner(
            title: "已排队",
            message: "等待 QMT Agent 委托回报；排队不代表券商受理或成交。",
            status: .ready
          )
        } else if let errorMessage {
          QuantXStatusBanner(
            title: "无法生成安全预览",
            message: errorMessage,
            status: .blocked
          )
        }

        QuantXStatusBanner(
          title: "先预览，后确认",
          message: "服务端会校验账户、可用资金或可卖量、价格与风控；只有短时票据通过生物识别后才能排队。",
          status: .attention
        )
      }
      .padding(QuantXTheme.Spacing.large)
    }
    .background(QuantXTheme.canvasBackground)
    .scrollDismissesKeyboard(.interactively)
    .navigationTitle(direction.title)
    .navigationBarTitleDisplayMode(.inline)
    .safeAreaInset(edge: .bottom) {
      previewAction
    }
    .sheet(item: $previewTicket) { preview in
      ManualOrderConfirmationSheet(preview: preview) {
        queued = true
      }
    }
    .onChange(of: instrumentCode) { _, _ in invalidateQueuedState() }
    .onChange(of: quoteType) { _, newValue in
      if newValue == .best { limitPriceText = "" }
      invalidateQueuedState()
    }
    .onChange(of: volumeText) { _, _ in invalidateQueuedState() }
    .onChange(of: limitPriceText) { _, _ in invalidateQueuedState() }
    .onDisappear {
      previewTicket = nil
    }
    .onChange(of: scenePhase) { _, newPhase in
      if newPhase == .background, !model.manualOrderInProgress {
        previewTicket = nil
      }
    }
  }

  private var accountScope: some View {
    QuantXCard {
      HStack(alignment: .top, spacing: QuantXTheme.Spacing.medium) {
        Image(systemName: "person.crop.circle.badge.checkmark")
          .font(.title2)
          .foregroundStyle(model.primaryTradingAccountID == nil ? QuantXTheme.warning : QuantXTheme.accent)
          .accessibilityHidden(true)
        VStack(alignment: .leading, spacing: 4) {
          Text("当前主账户")
            .font(.headline)
          if let accountID = model.primaryTradingAccountID {
            Text(masked(accountID))
              .font(.subheadline.monospaced())
              .foregroundStyle(QuantXTheme.secondaryText)
              .accessibilityLabel("主账户尾号 \(spokenDigits(String(accountID.suffix(4))))")
          } else {
            Text(model.manualOrderAvailabilityMessage ?? "主账户尚未完成同步")
              .font(.subheadline)
              .foregroundStyle(QuantXTheme.warning)
              .fixedSize(horizontal: false, vertical: true)
          }
        }
        Spacer()
        if model.primaryTradingAccountID == nil {
          Button("刷新") {
            Task { await model.refreshPortfolio() }
          }
          .buttonStyle(.bordered)
        }
      }
    }
  }

  private var ticketForm: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.large) {
        SectionTitle(title: "委托票据", subtitle: "仅生成预览，不会直接下单")

        VStack(alignment: .leading, spacing: 7) {
          Text("证券代码")
            .font(.subheadline.weight(.semibold))
          TextField("例如 600519.SH", text: $instrumentCode)
            .textInputAutocapitalization(.characters)
            .autocorrectionDisabled()
            .textFieldStyle(.roundedBorder)
            .font(.body.monospaced())
            .accessibilityIdentifier("manual-order-instrument")
          Text("必须包含 .SH、.SZ 或 .BJ 市场后缀")
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        }

        VStack(alignment: .leading, spacing: 7) {
          Text("报价方式")
            .font(.subheadline.weight(.semibold))
          Picker("报价方式", selection: $quoteType) {
            ForEach(ManualOrderQuoteType.allCases) { type in
              Text(type.title).tag(type)
            }
          }
          .pickerStyle(.segmented)
          Text(
            quoteType == .best
              ? "不携带限价，由服务端按 A 股合法对手方最优价规则校验。"
              : "服务端仍会校验价格步长、涨跌停与行情新鲜度。"
          )
          .font(.caption)
          .foregroundStyle(QuantXTheme.secondaryText)
          .fixedSize(horizontal: false, vertical: true)
        }

        if quoteType == .limit, !dynamicTypeSize.isAccessibilitySize {
          HStack(alignment: .top, spacing: QuantXTheme.Spacing.medium) {
            volumeInput
            inputField(
              title: "限价（元）",
              placeholder: "0.00",
              text: $limitPriceText,
              keyboardType: .decimalPad,
              identifier: "manual-order-limit-price"
            )
          }
        } else {
          VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
            volumeInput
            if quoteType == .limit {
              inputField(
                title: "限价（元）",
                placeholder: "0.00",
                text: $limitPriceText,
                keyboardType: .decimalPad,
                identifier: "manual-order-limit-price"
              )
            }
          }
        }
      }
    }
  }

  private var volumeInput: some View {
    inputField(
      title: "数量（股）",
      placeholder: "100",
      text: $volumeText,
      keyboardType: .numberPad,
      identifier: "manual-order-volume"
    )
  }

  private func inputField(
    title: String,
    placeholder: String,
    text: Binding<String>,
    keyboardType: UIKeyboardType,
    identifier: String
  ) -> some View {
    VStack(alignment: .leading, spacing: 7) {
      Text(title)
        .font(.subheadline.weight(.semibold))
      TextField(placeholder, text: text)
        .keyboardType(keyboardType)
        .textFieldStyle(.roundedBorder)
        .font(.body.monospacedDigit())
        .accessibilityIdentifier(identifier)
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }

  private var previewAction: some View {
    VStack(spacing: 7) {
      Button {
        Task { await requestPreview() }
      } label: {
        if previewInProgress {
          ProgressView()
            .frame(maxWidth: .infinity)
        } else {
          Label("获取服务器预览", systemImage: "doc.text.magnifyingglass")
            .frame(maxWidth: .infinity)
        }
      }
      .buttonStyle(.borderedProminent)
      .controlSize(.large)
      .tint(direction == .buy ? QuantXTheme.positive : QuantXTheme.negative)
      .disabled(previewInProgress || model.manualOrderInProgress || !model.canPlaceManualOrders)
      .accessibilityHint("只请求短时预览票据，不会提交委托")

      Text("每次预览使用独立幂等键；预览前不会提交委托。")
        .font(.caption2)
        .foregroundStyle(QuantXTheme.secondaryText)
    }
    .padding(.horizontal, QuantXTheme.Spacing.large)
    .padding(.vertical, QuantXTheme.Spacing.medium)
    .background(.ultraThinMaterial)
  }

  private func requestPreview() async {
    errorMessage = nil
    queued = false
    guard let volume = Int(volumeText), volume > 0 else {
      errorMessage = "请输入有效的正整数委托数量"
      return
    }
    let limitPrice: Double?
    switch quoteType {
    case .limit:
      guard let parsed = Double(limitPriceText), parsed.isFinite, parsed > 0 else {
        errorMessage = "限价委托必须填写有效价格"
        return
      }
      limitPrice = parsed
    case .best:
      limitPrice = nil
    }

    previewInProgress = true
    defer { previewInProgress = false }
    do {
      previewTicket = try await model.previewManualOrder(
        instrumentCode: instrumentCode,
        direction: direction,
        quoteType: quoteType,
        volume: volume,
        limitPrice: limitPrice,
        idempotencyKey: UUID()
      )
    } catch is CancellationError {
      return
    } catch {
      errorMessage = (error as? LocalizedError)?.errorDescription ?? "手动委托预览失败，请稍后重试"
    }
  }

  private func invalidateQueuedState() {
    queued = false
    errorMessage = nil
  }

  private func masked(_ accountID: String) -> String {
    accountID.count > 4 ? "•••• \(accountID.suffix(4))" : accountID
  }

  private func spokenDigits(_ value: String) -> String {
    let names: [Character: String] = [
      "0": "零", "1": "一", "2": "二", "3": "三", "4": "四",
      "5": "五", "6": "六", "7": "七", "8": "八", "9": "九",
    ]
    return value.map { names[$0] ?? String($0) }.joined(separator: " ")
  }
}

private struct ManualOrderConfirmationSheet: View {
  @Environment(\.dismiss) private var dismiss
  @EnvironmentObject private var model: AppModel

  let preview: ManualOrderPreviewTicket
  let onQueued: () -> Void

  @State private var errorMessage: String?
  @State private var confirmation: ManualOrderQueueConfirmation?

  var body: some View {
    NavigationStack {
      ScrollView {
        VStack(alignment: .leading, spacing: QuantXTheme.Spacing.large) {
          QuantXStatusBanner(
            title: "核对服务器预览",
            message: "Face ID / Touch ID 只确认本票据；排队不代表券商受理或成交。",
            status: .attention
          )
          if preview.wasCapped {
            QuantXStatusBanner(
              title: "风控已缩减委托数量",
              message: "请求 \(preview.requestedVolume.formatted()) 股，合法数量为 \(preview.finalVolume.formatted()) 股。确认只会提交合法数量。",
              status: .attention
            )
          }
          previewDetails
          warnings
          outcome
        }
        .padding(QuantXTheme.Spacing.large)
      }
      .background(QuantXTheme.canvasBackground)
      .navigationTitle("手动委托确认")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .cancellationAction) {
          Button("取消") { dismiss() }
            .disabled(model.manualOrderInProgress)
        }
      }
      .safeAreaInset(edge: .bottom) {
        actionBar
      }
      .interactiveDismissDisabled(model.manualOrderInProgress)
    }
  }

  private var previewDetails: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        HStack(alignment: .firstTextBaseline) {
          Text(preview.instrumentCode)
            .font(.title3.weight(.bold).monospaced())
          Spacer()
          Text(preview.direction.title)
            .font(.headline)
            .foregroundStyle(preview.direction == .buy ? QuantXTheme.positive : QuantXTheme.negative)
        }
        Divider()
        detailRow("主账户", masked(preview.accountID))
        detailRow("报价方式", preview.quoteType.title)
        detailRow("请求数量", "\(preview.requestedVolume.formatted()) 股")
        detailRow("合法数量", "\(preview.finalVolume.formatted()) 股")
        detailRow(
          preview.quoteType == .limit ? "委托限价" : "预览参考价",
          PortfolioFormatters.decimal(preview.limitPrice ?? preview.referencePrice)
        )
        if preview.quoteType == .limit {
          detailRow("行情参考价", PortfolioFormatters.decimal(preview.referencePrice))
        }
        detailRow("预估金额", PortfolioFormatters.currency(preview.estimatedAmount))
        detailRow("预估费用", PortfolioFormatters.currency(preview.estimatedFees))
        detailRow("可用资金", PortfolioFormatters.currency(preview.availableCash))
        if let availableVolume = preview.availableVolume {
          detailRow("可卖数量", "\(availableVolume.formatted()) 股")
        }
        detailRow("执行模式", preview.executionMode)
        detailRow("风控结果", preview.wasCapped ? "已缩量" : "允许")
        detailRow("风控原因", preview.riskReasonCode)
        detailRow("风控编号", preview.riskDecisionID)
        detailRow(
          "报价时间",
          preview.quoteTimestamp.formatted(date: .omitted, time: .standard)
        )
        if !preview.riskReasonDetail.isEmpty {
          Text(preview.riskReasonDetail)
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
            .fixedSize(horizontal: false, vertical: true)
        }

        TimelineView(.periodic(from: .now, by: 1)) { context in
          detailRow("票据有效期", expiryText(at: context.date))
            .foregroundStyle(preview.isExpired(at: context.date) ? QuantXTheme.warning : .primary)
        }
      }
    }
  }

  private var warnings: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: 10) {
        Label("确认前检查", systemImage: "shield.checkered")
          .font(.headline)
        if preview.warnings.isEmpty {
          Text("服务端未返回额外提示，确认时仍会重新执行统一风控。")
            .font(.subheadline)
            .foregroundStyle(QuantXTheme.secondaryText)
        } else {
          ForEach(Array(preview.warnings.enumerated()), id: \.offset) { _, warning in
            Label(warning, systemImage: "exclamationmark.circle")
              .font(.subheadline)
              .fixedSize(horizontal: false, vertical: true)
          }
        }
      }
    }
  }

  @ViewBuilder
  private var outcome: some View {
    if confirmation != nil {
      QuantXStatusBanner(
        title: "已排队",
        message: "请返回委托与成交页面等待 QMT Agent 回报。",
        status: .ready
      )
      .accessibilityIdentifier("manual-order-queued")
    } else if let errorMessage {
      QuantXStatusBanner(
        title: "确认失败",
        message: errorMessage,
        status: .blocked
      )
    }
  }

  private var actionBar: some View {
    VStack(spacing: 7) {
      if confirmation == nil {
        TimelineView(.periodic(from: .now, by: 1)) { context in
          Button {
            Task { await confirm() }
          } label: {
            if model.manualOrderInProgress {
              ProgressView()
                .frame(maxWidth: .infinity)
            } else {
              Label(
                "Face ID / Touch ID 确认 \(preview.finalVolume.formatted()) 股",
                systemImage: "faceid"
              )
                .frame(maxWidth: .infinity)
            }
          }
          .buttonStyle(.borderedProminent)
          .controlSize(.large)
          .tint(QuantXTheme.approvalAction)
          .disabled(model.manualOrderInProgress || preview.isExpired(at: context.date))
          .accessibilityHint("生物识别成功后消费一次性票据并将委托加入队列")
        }
      } else {
        Button("完成") {
          onQueued()
          dismiss()
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .frame(maxWidth: .infinity)
      }
      Text("最终状态以券商委托与成交回报为准")
        .font(.caption2)
        .foregroundStyle(QuantXTheme.secondaryText)
    }
    .padding(.horizontal, QuantXTheme.Spacing.large)
    .padding(.vertical, QuantXTheme.Spacing.medium)
    .background(.ultraThinMaterial)
  }

  private func detailRow(_ title: String, _ value: String) -> some View {
    HStack(alignment: .firstTextBaseline) {
      Text(title)
        .foregroundStyle(QuantXTheme.secondaryText)
      Spacer(minLength: QuantXTheme.Spacing.large)
      Text(value)
        .fontWeight(.semibold)
        .multilineTextAlignment(.trailing)
        .monospacedDigit()
        .lineLimit(2)
        .minimumScaleFactor(0.72)
    }
    .font(.subheadline)
  }

  private func masked(_ accountID: String) -> String {
    accountID.count > 4 ? "•••• \(accountID.suffix(4))" : accountID
  }

  private func expiryText(at date: Date) -> String {
    let seconds = max(0, Int(preview.challengeExpiresAt.timeIntervalSince(date).rounded(.up)))
    return seconds > 0 ? "\(seconds) 秒" : "已过期"
  }

  private func confirm() async {
    errorMessage = nil
    do {
      confirmation = try await model.confirmManualOrder(preview)
    } catch is CancellationError {
      return
    } catch {
      errorMessage = (error as? LocalizedError)?.errorDescription ?? "手动委托确认失败，请重新预览"
    }
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
