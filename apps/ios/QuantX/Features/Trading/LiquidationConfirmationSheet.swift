import SwiftUI

struct LiquidationConfirmationSheet: View {
  @Environment(\.dismiss) private var dismiss
  @ObservedObject var store: LiquidationStore

  let preview: LiquidationPreviewTicket

  @State private var confirmation: LiquidationConfirmation?
  @State private var errorMessage: String?
  @State private var resultRecoveryAvailable = false
  @State private var transmissionStarted = false
  @State private var recoveryAuthorization: LiquidationResultRecoveryAuthorization?

  var body: some View {
    NavigationStack {
      ScrollView {
        VStack(alignment: .leading, spacing: QuantXTheme.Spacing.large) {
          QuantXStatusBanner(
            title: "服务器清仓预览",
            message: "本页只核对已签名的账户、持仓快照、数量与策略；确认只请求 Engine 创建退出计划。",
            status: preview.executionMode == .live ? .attention : .working
          )
          previewSummary
          itemList
          warningList
          if let confirmation {
            confirmationResult(confirmation)
          }
          if let errorMessage {
            QuantXStatusBanner(
              title: resultRecoveryAvailable ? "结果暂不确定" : "确认未完成",
              message: errorMessage,
              status: resultRecoveryAvailable ? .attention : .blocked
            )
          }
          QuantXStatusBanner(
            title: "成交状态不在本页确认",
            message: "退出计划创建成功也不代表委托已报送、券商已受理或已经成交；最终事实仍以 QMT Agent 回报为准。",
            status: .attention
          )
        }
        .padding(QuantXTheme.Spacing.large)
      }
      .background(QuantXTheme.canvasBackground)
      .navigationTitle("核对清仓计划")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .cancellationAction) {
          Button("关闭") { dismiss() }
            .disabled(store.operationInProgress)
        }
      }
      .safeAreaInset(edge: .bottom) {
        TimelineView(.periodic(from: .now, by: 1)) { context in
          confirmationActionBar(at: context.date)
        }
      }
    }
    .interactiveDismissDisabled(store.operationInProgress)
    .presentationDragIndicator(.visible)
  }

  private var previewSummary: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        SectionTitle(title: "签名范围", subtitle: "所有字段均来自服务器预览")
        detailRow("账户", masked(preview.accountID))
        detailRow("范围", preview.scope.title)
        detailRow("完成策略", preview.completionStrategy.title)
        detailRow("冲突策略", preview.conflictStrategy.title)
        detailRow("执行模式", preview.executionMode.title)
        detailRow("纳入 / 跳过", "\(preview.includedCount) / \(preview.skippedCount)")
        detailRow("持仓快照", shortSnapshotVersion)
        detailRow("账户快照时间", preview.accountUpdatedAt.formatted(date: .abbreviated, time: .standard))
        Divider()
        TimelineView(.periodic(from: .now, by: 1)) { context in
          detailRow("首次确认剩余", expiryText(at: context.date))
            .foregroundStyle(
              preview.isExpired(at: context.date) && !resultRecoveryAvailable
                ? QuantXTheme.critical
                : Color.primary
            )
        }
      }
    }
  }

  private var itemList: some View {
    VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
      SectionTitle(
        title: "逐只核对",
        subtitle: "纳入、跳过、数量保护与冲突均不由客户端推算"
      )
      ForEach(preview.items) { item in
        LiquidationPreviewItemCard(item: item)
      }
    }
  }

  @ViewBuilder
  private var warningList: some View {
    if !preview.warnings.isEmpty {
      QuantXCard {
        VStack(alignment: .leading, spacing: QuantXTheme.Spacing.small) {
          SectionTitle(title: "服务器警告", subtitle: "确认前必须逐项阅读")
          ForEach(Array(preview.warnings.enumerated()), id: \.offset) { _, warning in
            Label(warning, systemImage: "exclamationmark.triangle.fill")
              .font(.caption)
              .foregroundStyle(QuantXTheme.warning)
              .fixedSize(horizontal: false, vertical: true)
          }
        }
      }
    }
  }

  @ViewBuilder
  private func confirmationResult(_ value: LiquidationConfirmation) -> some View {
    QuantXStatusBanner(
      title: value.status.title,
      message: value.outcomeMessage,
      status: confirmationStatus(value)
    )

    if !value.plans.isEmpty {
      QuantXCard {
        VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
          SectionTitle(
            title: value.isPartial ? "部分计划创建结果" : "计划创建结果",
            subtitle: "已创建 \(value.createdCount) · 未创建 \(value.failedCount)"
          )
          ForEach(value.plans) { plan in
            LiquidationPlanOutcomeRow(plan: plan)
          }
        }
      }
    }
  }

  private func confirmationActionBar(at date: Date) -> some View {
    VStack(spacing: 7) {
      if isTerminal {
        Button("完成") { dismiss() }
          .buttonStyle(.borderedProminent)
          .controlSize(.large)
          .frame(maxWidth: .infinity)
      } else {
        Button {
          Task { await confirm() }
        } label: {
          if store.operationInProgress {
            ProgressView().frame(maxWidth: .infinity)
          } else {
            Label(actionTitle, systemImage: "faceid")
              .frame(maxWidth: .infinity)
          }
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .tint(preview.executionMode == .live ? QuantXTheme.approvalAction : QuantXTheme.accent)
        .disabled(
          store.operationInProgress
            || (!resultRecoveryAvailable && preview.isExpired(at: date))
            || store.confirmationUnavailableReason(for: preview) != nil
        )
        .accessibilityHint(actionHint)
      }

      if let unavailable = store.confirmationUnavailableReason(for: preview) {
        Text(unavailable)
          .font(.caption2)
          .foregroundStyle(QuantXTheme.critical)
          .fixedSize(horizontal: false, vertical: true)
      } else if resultRecoveryAvailable {
        Text("每次刷新结果仍会重新要求 Face ID / Touch ID；不会扩大签名范围。")
          .font(.caption2)
          .foregroundStyle(QuantXTheme.secondaryText)
      } else if preview.isExpired(at: date) {
        Text("首次确认挑战已过期，请关闭并重新获取服务器预览。")
          .font(.caption2)
          .foregroundStyle(QuantXTheme.critical)
      } else {
        Text("每次确认均要求 Face ID / Touch ID；挑战和令牌只保存在此页面内存中。")
          .font(.caption2)
          .foregroundStyle(QuantXTheme.secondaryText)
      }
    }
    .padding(.horizontal, QuantXTheme.Spacing.large)
    .padding(.vertical, QuantXTheme.Spacing.medium)
    .background(.ultraThinMaterial)
  }

  private var isTerminal: Bool {
    guard let confirmation else { return false }
    return !confirmation.status.allowsRecovery
  }

  private var actionTitle: String {
    if resultRecoveryAvailable {
      return "生物识别刷新计划结果"
    }
    return preview.executionMode == .live
      ? "生物识别确认实盘创建计划"
      : "生物识别确认模拟创建计划"
  }

  private var actionHint: String {
    resultRecoveryAvailable
      ? "重新读取同一清仓挑战的计划创建结果"
      : "验证身份后消费一次性挑战，请求 Engine 创建退出计划"
  }

  private var shortSnapshotVersion: String {
    let value = preview.snapshotVersion
    return value.count > 12 ? "\(value.prefix(12))…" : value
  }

  private func confirmationStatus(_ value: LiquidationConfirmation) -> QuantXSemanticStatus {
    switch value.status {
    case .pending, .processing: .working
    case .succeeded:
      value.failedCount == 0 ? .ready : (value.createdCount > 0 ? .attention : .blocked)
    case .failed: .blocked
    case .unknown: .attention
    }
  }

  private func confirm() async {
    errorMessage = nil
    transmissionStarted = false
    do {
      let value = try await store.confirm(
        preview,
        recoveryAuthorization: resultRecoveryAvailable ? recoveryAuthorization : nil,
        onTransmissionStarted: { authorization in
          transmissionStarted = true
          recoveryAuthorization = authorization
        }
      )
      confirmation = value
      resultRecoveryAvailable = value.status.allowsRecovery
      if !resultRecoveryAvailable {
        recoveryAuthorization = nil
      }
    } catch is CancellationError {
      if transmissionStarted, recoveryAuthorization != nil {
        resultRecoveryAvailable = true
        errorMessage = "确认请求可能已送达；请使用生物识别刷新同一挑战的计划创建结果"
      }
      return
    } catch {
      let canRecover = LiquidationStore.allowsResultRecovery(after: error)
        || (transmissionStarted && isAmbiguousResponse(error))
      resultRecoveryAvailable = canRecover
      if !canRecover {
        recoveryAuthorization = nil
      }
      errorMessage = error.localizedDescription
    }
  }

  private func isAmbiguousResponse(_ error: Error) -> Bool {
    guard let repositoryError = error as? LiquidationRepositoryError else { return false }
    if case .invalidResponse = repositoryError { return true }
    return false
  }

  private func detailRow(_ title: String, _ value: String) -> some View {
    HStack(alignment: .firstTextBaseline) {
      Text(title)
        .foregroundStyle(QuantXTheme.secondaryText)
      Spacer(minLength: QuantXTheme.Spacing.medium)
      Text(value)
        .fontWeight(.semibold)
        .multilineTextAlignment(.trailing)
        .lineLimit(3)
        .minimumScaleFactor(0.72)
    }
    .font(.subheadline)
  }

  private func masked(_ accountID: String) -> String {
    accountID.count > 4 ? "•••• \(accountID.suffix(4))" : accountID
  }

  private func expiryText(at date: Date) -> String {
    if resultRecoveryAvailable, preview.isExpired(at: date) {
      return "挑战已消费，可刷新结果"
    }
    let seconds = max(0, Int(preview.challengeExpiresAt.timeIntervalSince(date).rounded(.up)))
    return seconds > 0 ? "\(seconds) 秒" : "已过期"
  }
}

private struct LiquidationPreviewItemCard: View {
  @Environment(\.dynamicTypeSize) private var dynamicTypeSize

  let item: LiquidationPreviewItem

  var body: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.medium) {
        HStack(alignment: .top) {
          VStack(alignment: .leading, spacing: 3) {
            Text(item.displayName)
              .font(.headline)
            Text(item.instrumentCode)
              .font(.caption.monospaced())
              .foregroundStyle(QuantXTheme.secondaryText)
          }
          Spacer(minLength: 8)
          StatusBadge(
            title: item.included ? "纳入" : "跳过",
            systemImage: item.included ? "checkmark.shield.fill" : "minus.circle.fill",
            color: item.included ? QuantXTheme.negative : QuantXTheme.warning
          )
        }

        LazyVGrid(
          columns: metricColumns,
          spacing: QuantXTheme.Spacing.small
        ) {
          metric("总持仓", item.totalVolume)
          metric("当前可卖", item.availableVolume)
          metric("冻结", item.frozenVolume)
          metric("T+1 不可卖", item.t1UnavailableVolume)
          metric("现有保护", item.protectedVolume)
          metric("在途卖出", item.pendingSellVolume)
        }

        HStack(alignment: .firstTextBaseline) {
          Text("本次最大保护量")
            .foregroundStyle(QuantXTheme.secondaryText)
          Spacer(minLength: 8)
          Text("\(item.maxProtectedVolume.formatted()) 股")
            .fontWeight(.bold)
            .monospacedDigit()
        }
        .font(.subheadline)

        Divider()
        VStack(alignment: .leading, spacing: 4) {
          Text(item.reasonCode)
            .font(.caption.weight(.semibold).monospaced())
          Text(item.reasonDetail)
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
            .fixedSize(horizontal: false, vertical: true)
        }

        if !item.conflicts.isEmpty {
          Divider()
          Text("冲突退出计划")
            .font(.subheadline.weight(.semibold))
          ForEach(item.conflicts) { conflict in
            LiquidationConflictRow(conflict: conflict)
          }
        }
      }
    }
  }

  private var metricColumns: [GridItem] {
    if dynamicTypeSize.isAccessibilitySize {
      return [GridItem(.flexible())]
    }
    return [GridItem(.flexible()), GridItem(.flexible())]
  }

  private func metric(_ title: String, _ value: Int) -> some View {
    VStack(alignment: .leading, spacing: 2) {
      Text(title)
        .font(.caption2)
        .foregroundStyle(QuantXTheme.secondaryText)
      Text("\(value.formatted()) 股")
        .font(.caption.weight(.semibold).monospacedDigit())
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .padding(QuantXTheme.Spacing.small)
    .background(QuantXTheme.elevatedBackground, in: RoundedRectangle(cornerRadius: 9))
    .accessibilityElement(children: .combine)
  }
}

private struct LiquidationConflictRow: View {
  let conflict: LiquidationConflict

  var body: some View {
    VStack(alignment: .leading, spacing: 5) {
      HStack {
        Text(maskedPlanID)
          .font(.caption.weight(.semibold).monospaced())
        Spacer(minLength: 8)
        StatusBadge(
          title: conflict.pending ? "待处理" : conflict.status,
          systemImage: conflict.pending ? "clock.fill" : "shield.fill",
          color: conflict.pending ? QuantXTheme.warning : QuantXTheme.secondaryText
        )
      }
      Text("来源 \(conflict.sourceType) · 剩余 \(conflict.remainingVolume.formatted()) 股 · 配置 v\(conflict.configVersion)")
        .font(.caption2)
        .foregroundStyle(QuantXTheme.secondaryText)
        .fixedSize(horizontal: false, vertical: true)
    }
    .padding(QuantXTheme.Spacing.small)
    .background(QuantXTheme.elevatedBackground, in: RoundedRectangle(cornerRadius: 10))
    .accessibilityElement(children: .combine)
  }

  private var maskedPlanID: String {
    conflict.planID.count > 8 ? "计划 …\(conflict.planID.suffix(8))" : "计划 \(conflict.planID)"
  }
}

private struct LiquidationPlanOutcomeRow: View {
  let plan: LiquidationPlanOutcome

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack {
        Text(plan.instrumentCode)
          .font(.subheadline.weight(.semibold).monospaced())
        Spacer(minLength: 8)
        StatusBadge(
          title: plan.success ? "退出计划已创建" : "未创建",
          systemImage: plan.success ? "checkmark.circle.fill" : "xmark.circle.fill",
          color: plan.success ? QuantXTheme.negative : QuantXTheme.critical
        )
      }
      if let protectedVolume = plan.protectedVolume {
        Text("计划保护量 \(protectedVolume.formatted()) 股")
          .font(.caption.monospacedDigit())
      }
      if !plan.conflictPlanIDs.isEmpty {
        Text("处理冲突计划 \(plan.conflictPlanIDs.count) 个")
          .font(.caption)
          .foregroundStyle(QuantXTheme.secondaryText)
      }
      if let error = plan.error {
        Text(error)
          .font(.caption)
          .foregroundStyle(QuantXTheme.critical)
          .fixedSize(horizontal: false, vertical: true)
      }
    }
    .padding(QuantXTheme.Spacing.small)
    .background(QuantXTheme.elevatedBackground, in: RoundedRectangle(cornerRadius: 10))
    .accessibilityElement(children: .combine)
  }
}
