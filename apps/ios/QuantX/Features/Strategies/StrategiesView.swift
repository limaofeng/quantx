import SwiftUI

struct StrategiesView: View {
  @EnvironmentObject private var model: AppModel
  var embeddedInNavigation = false

  @ViewBuilder
  var body: some View {
    if embeddedInNavigation {
      rootContent
        .task { await loadIfNeeded() }
    } else {
      NavigationStack {
        rootContent
      }
      .task { await loadIfNeeded() }
    }
  }

  private var rootContent: some View {
    content
      .background(QuantXTheme.canvasBackground)
      .navigationTitle("策略")
      .navigationDestination(for: StrategyMonitorItem.self) { instance in
        StrategyMonitorDetailView(instanceID: instance.id)
      }
      .toolbar {
        if model.strategyRefreshInProgress,
          model.strategyState.snapshot != nil
        {
          ToolbarItem(placement: .topBarTrailing) {
            ProgressView()
              .accessibilityLabel("正在刷新策略")
          }
        }
      }
  }

  private func loadIfNeeded() async {
    if case .idle = model.strategyState {
      await model.refreshStrategies()
    }
  }

  @ViewBuilder
  private var content: some View {
    switch model.strategyState {
    case .unavailable(let reason):
      ContentUnavailableView {
        Label("策略只读查询不可用", systemImage: "lock.shield.fill")
      } description: {
        Text(reason)
      }
    case .idle, .loading:
      ProgressView("正在读取策略快照…")
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityLabel("正在读取策略快照")
    case .loaded(let snapshot, let refreshWarning):
      loadedContent(snapshot: snapshot, refreshWarning: refreshWarning)
    case .failed(let message):
      ContentUnavailableView {
        Label("无法读取策略", systemImage: "wifi.exclamationmark")
      } description: {
        Text(message)
      } actions: {
        retryButton
      }
    }
  }

  private func loadedContent(
    snapshot: StrategyMonitorSnapshot,
    refreshWarning: String?
  ) -> some View {
    ScrollView {
      LazyVStack(spacing: 14) {
        if let refreshWarning {
          RefreshWarningView(message: refreshWarning)
        }

        HStack(alignment: .firstTextBaseline) {
          VStack(alignment: .leading, spacing: 4) {
            Text("策略实例")
              .font(.headline)
            Text("服务端参数 allowlist、版本锁与安全生命周期控制")
              .font(.caption)
              .foregroundStyle(QuantXTheme.secondaryText)
          }
          Spacer()
          Text("\(snapshot.instances.count) 项")
            .font(.subheadline)
            .foregroundStyle(QuantXTheme.secondaryText)
            .monospacedDigit()
        }

        if snapshot.instances.isEmpty {
          QuantXCard {
            ContentUnavailableView {
              Label("暂无策略实例", systemImage: "waveform.path.ecg")
            } description: {
              Text("后端当前没有返回可读取的策略实例。")
            }
          }
        } else {
          ForEach(snapshot.instances) { instance in
            NavigationLink(value: instance) {
              QuantXCard {
                StrategyMonitorRow(instance: instance)
              }
            }
            .buttonStyle(.plain)
            .frame(minHeight: 44)
            .accessibilityHint("查看策略参数和安全控制")
          }
        }

        HStack {
          DataFreshnessView(updatedAt: snapshot.fetchedAt)
          Spacer()
          Text(snapshot.fetchedAt.formatted(date: .omitted, time: .standard))
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
            .monospacedDigit()
        }
      }
      .padding(16)
    }
    .refreshable {
      await model.refreshStrategies()
    }
  }

  private var retryButton: some View {
    Button("重新加载") {
      Task { await model.refreshStrategies() }
    }
    .buttonStyle(.borderedProminent)
    .controlSize(.large)
    .disabled(model.strategyRefreshInProgress)
  }
}

private struct StrategyMonitorRow: View {
  let instance: StrategyMonitorItem

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack(alignment: .top, spacing: 12) {
        VStack(alignment: .leading, spacing: 4) {
          Text(instance.displayName)
            .font(.headline)
            .foregroundStyle(.primary)
          Text("\(instance.instrumentCode) · \(instance.modeDisplayName)")
            .font(.subheadline)
            .foregroundStyle(QuantXTheme.secondaryText)
        }
        Spacer(minLength: 8)
        StatusBadge(
          title: instance.statusDisplayName,
          systemImage: statusImage,
          color: statusColor
        )
      }

      if let executionStatus = instance.latestExecutionStatus,
        !executionStatus.isEmpty
      {
        Text("最近执行：\(executionStatus)")
          .font(.caption)
          .foregroundStyle(QuantXTheme.secondaryText)
          .fixedSize(horizontal: false, vertical: true)
      }
    }
    .accessibilityElement(children: .combine)
  }

  private var statusImage: String {
    switch instance.status {
    case "RUNNING": "play.circle.fill"
    case "ERROR": "exclamationmark.triangle.fill"
    case "PAUSED": "pause.circle.fill"
    case "COMPLETED": "checkmark.circle.fill"
    default: "circle.fill"
    }
  }

  private var statusColor: Color {
    switch instance.status {
    case "RUNNING", "COMPLETED": QuantXTheme.online
    case "ERROR": QuantXTheme.warning
    case "PENDING": QuantXTheme.accent
    default: QuantXTheme.secondaryText
    }
  }
}

private struct StrategyMonitorDetailView: View {
  @EnvironmentObject private var model: AppModel
  let instanceID: String

  var body: some View {
    Group {
      if let instance = model.strategyState.snapshot?.instances.first(where: { $0.id == instanceID }
      ) {
        StrategyWorkspaceDetailContent(
          instance: instance,
          workspace: model.strategyWorkspace
        )
      } else {
        ContentUnavailableView {
          Label("策略实例已变化", systemImage: "arrow.clockwise.circle")
        } description: {
          Text("账户或策略快照已刷新，请返回列表重新选择。")
        }
      }
    }
    .navigationTitle(
      model.strategyState.snapshot?.instances.first(where: { $0.id == instanceID })?.displayName
        ?? "策略详情"
    )
    .navigationBarTitleDisplayMode(.inline)
  }
}

private struct StrategyWorkspaceDetailContent: View {
  let instance: StrategyMonitorItem
  @ObservedObject var workspace: StrategyWorkspace

  @State private var directConfirmation: StrategyLifecycleControl?

  var body: some View {
    Form {
      statusSection
      feedbackSection
      parameterSection
      lifecycleSection
      safetySection
    }
    .task(id: instance.id) {
      await workspace.select(instance)
    }
    .onDisappear {
      workspace.clearSelection(instanceID: instance.id)
    }
    .confirmationDialog(
      directConfirmation?.title ?? "确认策略操作",
      isPresented: Binding(
        get: { directConfirmation != nil },
        set: { if !$0 { directConfirmation = nil } }
      ),
      titleVisibility: .visible
    ) {
      if let control = directConfirmation {
        Button(control.title, role: control == .pause ? .destructive : nil) {
          directConfirmation = nil
          Task { try? await workspace.performDirectControl(control, instance: instance) }
        }
        Button("取消", role: .cancel) { directConfirmation = nil }
      }
    } message: {
      if directConfirmation == .pause {
        Text("暂停只停止新的策略决策；已有退出保护与已进入执行链路的委托继续由服务端管理。")
      } else {
        Text("恢复后策略会继续按服务端当前参数和风控规则运行。")
      }
    }
    .sheet(
      item: Binding(
        get: { workspace.pendingControl },
        set: { if $0 == nil { workspace.dismissPendingControl() } }
      )
    ) { preview in
      StrategyControlConfirmationSheet(
        preview: preview,
        workspace: workspace
      )
    }
  }

  private var statusSection: some View {
    Section("实例") {
      LabeledContent("名称", value: instance.displayName)
      LabeledContent("策略", value: instance.strategyName ?? instance.strategyKey)
      LabeledContent("标的", value: instance.instrumentCode)
      LabeledContent("模式", value: instance.modeDisplayName)
      LabeledContent("状态", value: instance.statusDisplayName)
      LabeledContent("参数版本", value: instance.parameterVersion)
      LabeledContent("最近执行", value: instance.latestExecutionStatus ?? "暂无")
      LabeledContent("更新时间") {
        Text(instance.updatedAt.formatted(date: .abbreviated, time: .standard))
          .monospacedDigit()
      }
    }
  }

  @ViewBuilder
  private var feedbackSection: some View {
    if let message = workspace.successMessage {
      Section {
        QuantXStatusBanner(title: "服务端已确认", message: message, status: .ready)
          .listRowInsets(EdgeInsets())
          .listRowBackground(Color.clear)
      }
    }
    if let message = workspace.errorMessage {
      Section {
        QuantXStatusBanner(title: "操作未完成", message: message, status: .attention)
          .listRowInsets(EdgeInsets())
          .listRowBackground(Color.clear)
      }
    }
  }

  @ViewBuilder
  private var parameterSection: some View {
    Section {
      switch workspace.parameterState {
      case .idle, .loading(_):
        HStack(spacing: 12) {
          ProgressView()
          Text("正在读取服务端移动参数…")
            .foregroundStyle(QuantXTheme.secondaryText)
        }
        .accessibilityElement(children: .combine)
      case .failed(_, let message):
        ContentUnavailableView {
          Label("移动参数不可用", systemImage: "slider.horizontal.3")
        } description: {
          Text(message)
        } actions: {
          Button("重新加载") {
            Task { await workspace.retryParameters() }
          }
        }
      case .loaded(let snapshot):
        if snapshot.parameters.isEmpty {
          Text("后端没有为此策略开放原生移动参数。")
            .foregroundStyle(QuantXTheme.secondaryText)
        } else {
          if let conflict = workspace.parameterConflict {
            NavigationLink {
              StrategyParameterConflictReviewView(
                instance: instance,
                workspace: workspace
              )
            } label: {
              VStack(alignment: .leading, spacing: 4) {
                Label("核对参数版本差异", systemImage: "arrow.trianglehead.2.clockwise.rotate.90")
                  .font(.subheadline.weight(.semibold))
                  .foregroundStyle(QuantXTheme.warning)
                Text(
                  "你的版本 v\(conflict.staleVersion) · 服务端 v\(conflict.serverVersion) · \(conflict.differences.count) 个字段差异"
                )
                .font(.caption)
                .foregroundStyle(QuantXTheme.secondaryText)
              }
            }
            .accessibilityHint("逐字段核对你的值与服务端值，并明确选择如何处理")
          }

          ForEach(snapshot.parameters) { parameter in
            StrategyMobileParameterRow(
              parameter: parameter,
              value: draftBinding(for: parameter),
              isValid: workspace.isDraftValid(for: parameter),
              isEnabled: workspace.parameterEditingUnavailableReason(for: instance) == nil
                && !workspace.operationInProgress
            )
          }

          if let reason = workspace.parameterEditingUnavailableReason(for: instance) {
            Label(reason, systemImage: "lock.fill")
              .font(.footnote)
              .foregroundStyle(QuantXTheme.secondaryText)
          } else if workspace.parameterConflict != nil {
            Label(
              "冲突前草稿仍保留；请进入差异页选择采用服务端值，或基于新版本重新提交。",
              systemImage: "exclamationmark.triangle.fill"
            )
            .font(.footnote)
            .foregroundStyle(QuantXTheme.warning)
          } else {
            HStack {
              Button("放弃修改") {
                workspace.discardParameterChanges()
              }
              .disabled(!workspace.hasUnsavedParameterChanges || workspace.operationInProgress)

              Spacer()

              Button {
                Task { try? await workspace.saveParameters(for: instance) }
              } label: {
                if workspace.operationInProgress {
                  ProgressView()
                } else {
                  Text("保存到服务端")
                }
              }
              .buttonStyle(.borderedProminent)
              .disabled(
                !workspace.hasUnsavedParameterChanges
                  || snapshot.parameters.contains(where: { !workspace.isDraftValid(for: $0) })
                  || workspace.operationInProgress
              )
            }
            .frame(minHeight: 44)
          }
        }
      }
    } header: {
      Text("移动参数")
    } footer: {
      Text("只展示服务端 typed allowlist；保存始终携带当前 configVersion，冲突时保留草稿并要求逐字段核对。")
    }
  }

  @ViewBuilder
  private var lifecycleSection: some View {
    Section("生命周期控制") {
      if instance.lifecycleControls.isEmpty {
        Text("当前模式与状态没有可用的移动控制动作。")
          .foregroundStyle(QuantXTheme.secondaryText)
      } else {
        ForEach(instance.lifecycleControls) { control in
          Button {
            if control.requiresLiveConfirmation {
              Task { try? await workspace.previewLiveControl(control, instance: instance) }
            } else {
              directConfirmation = control
            }
          } label: {
            HStack(spacing: 10) {
              Image(systemName: controlIcon(control))
                .frame(width: 22)
              VStack(alignment: .leading, spacing: 2) {
                Text(control.title)
                  .fontWeight(.semibold)
                Text(control.requiresLiveConfirmation ? "先获取实盘就绪预览，再逐次生物确认" : directDetail(control))
                  .font(.caption)
                  .foregroundStyle(QuantXTheme.secondaryText)
              }
              Spacer()
              if workspace.operationInProgress {
                ProgressView()
              } else {
                Image(systemName: "chevron.right")
                  .font(.caption.weight(.semibold))
                  .foregroundStyle(QuantXTheme.secondaryText)
              }
            }
            .frame(minHeight: 44)
          }
          .disabled(
            workspace.lifecycleUnavailableReason(for: control, instance: instance) != nil
              || workspace.operationInProgress
          )
          .accessibilityHint(
            workspace.lifecycleUnavailableReason(for: control, instance: instance)
              ?? (control.requiresLiveConfirmation ? "打开实盘策略安全确认" : directDetail(control))
          )

          if let reason = workspace.lifecycleUnavailableReason(for: control, instance: instance) {
            Text(reason)
              .font(.caption)
              .foregroundStyle(QuantXTheme.secondaryText)
          }
        }
      }
    }
  }

  private var safetySection: some View {
    Section("安全边界") {
      Label(
        "PAPER 暂停/恢复和风险降低的 LIVE 暂停直接调用服务端；LIVE 启动、恢复、克隆必须两阶段确认。",
        systemImage: "checkmark.shield.fill"
      )
      Label(
        "控制成功仅表示 Engine 已应用生命周期动作，不代表任何委托已报送或成交。",
        systemImage: "info.circle.fill"
      )
    }
    .font(.footnote)
    .foregroundStyle(QuantXTheme.secondaryText)
  }

  private func draftBinding(
    for parameter: StrategyMobileParameter
  ) -> Binding<StrategyMobileParameterValue> {
    Binding(
      get: { workspace.draftValues[parameter.key] ?? parameter.currentValue },
      set: { workspace.setDraftValue($0, for: parameter) }
    )
  }

  private func controlIcon(_ control: StrategyLifecycleControl) -> String {
    switch control {
    case .pause: "pause.circle.fill"
    case .resumePaper, .resumeLive: "play.circle.fill"
    case .startLive: "bolt.shield.fill"
    case .cloneToLive: "square.on.square"
    }
  }

  private func directDetail(_ control: StrategyLifecycleControl) -> String {
    control == .pause
      ? "停止新决策；已有退出保护继续运行"
      : "直接恢复 PAPER 实例"
  }
}

private struct StrategyMobileParameterRow: View {
  let parameter: StrategyMobileParameter
  @Binding var value: StrategyMobileParameterValue
  let isValid: Bool
  let isEnabled: Bool

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack(alignment: .firstTextBaseline) {
        Text(parameter.title)
          .font(.subheadline.weight(.semibold))
        Spacer()
        Text(parameter.riskLevel.title)
          .font(.caption2.weight(.semibold))
          .foregroundStyle(riskColor)
      }

      if !parameter.description.isEmpty {
        Text(parameter.description)
          .font(.caption)
          .foregroundStyle(QuantXTheme.secondaryText)
          .fixedSize(horizontal: false, vertical: true)
      }

      control
        .disabled(!isEnabled)

      HStack(alignment: .firstTextBaseline) {
        Text(constraintText)
        Spacer()
        Text(parameter.applyImmediately ? "允许立即应用" : "安全暂存")
      }
      .font(.caption2)
      .foregroundStyle(isValid ? QuantXTheme.secondaryText : QuantXTheme.critical)
    }
    .padding(.vertical, 4)
    .accessibilityElement(children: .contain)
  }

  @ViewBuilder
  private var control: some View {
    switch parameter.kind {
    case .boolean:
      Toggle("设置", isOn: booleanBinding)
        .labelsHidden()
        .accessibilityLabel(parameter.title)
    case .string where !parameter.enumValues.isEmpty:
      Picker(parameter.title, selection: stringBinding) {
        ForEach(parameter.enumValues, id: \.self) { option in
          Text(option).tag(option)
        }
      }
      .pickerStyle(.menu)
    case .string:
      TextField(parameter.title, text: stringBinding)
        .textInputAutocapitalization(.never)
        .autocorrectionDisabled()
    case .integer:
      TextField(parameter.title, value: integerBinding, format: .number)
        .keyboardType(.numbersAndPunctuation)
        .monospacedDigit()
    case .number:
      TextField(
        parameter.title,
        value: numberBinding,
        format: .number.precision(.fractionLength(0...8))
      )
      .keyboardType(.decimalPad)
      .monospacedDigit()
    }
  }

  private var booleanBinding: Binding<Bool> {
    Binding(
      get: { if case .boolean(let value) = value { value } else { false } },
      set: { value = .boolean($0) }
    )
  }

  private var stringBinding: Binding<String> {
    Binding(
      get: { if case .string(let value) = value { value } else { "" } },
      set: { value = .string($0) }
    )
  }

  private var integerBinding: Binding<Int> {
    Binding(
      get: { if case .integer(let value) = value { value } else { 0 } },
      set: { value = .integer($0) }
    )
  }

  private var numberBinding: Binding<Double> {
    Binding(
      get: { if case .number(let value) = value { value } else { 0 } },
      set: { value = .number($0) }
    )
  }

  private var constraintText: String {
    var parts: [String] = []
    if let minimum = parameter.minimum { parts.append("最小 \(format(minimum))") }
    if let maximum = parameter.maximum { parts.append("最大 \(format(maximum))") }
    if let step = parameter.step { parts.append("步长 \(format(step))") }
    if let unit = parameter.unit { parts.append(unit) }
    if !isValid { parts.append("当前值不符合约束") }
    return parts.isEmpty ? "由服务端校验" : parts.joined(separator: " · ")
  }

  private var riskColor: Color {
    switch parameter.riskLevel {
    case .low: QuantXTheme.online
    case .medium: QuantXTheme.warning
    case .high: QuantXTheme.critical
    }
  }

  private func format(_ value: Double) -> String {
    value.formatted(.number.precision(.fractionLength(0...8)))
  }
}

private struct StrategyParameterConflictReviewView: View {
  @Environment(\.dismiss) private var dismiss
  let instance: StrategyMonitorItem
  @ObservedObject var workspace: StrategyWorkspace

  @State private var showAdoptServerConfirmation = false

  var body: some View {
    Group {
      if let conflict = workspace.parameterConflict {
        Form {
          Section {
            QuantXStatusBanner(
              title: "草稿没有被覆盖",
              message:
                "保存使用的 v\(conflict.staleVersion) 已过期。下面的服务端 v\(conflict.serverVersion) 是当前真源。",
              status: .attention
            )
            .listRowInsets(EdgeInsets())
            .listRowBackground(Color.clear)
          }

          Section("逐字段差异") {
            if conflict.differences.isEmpty {
              Text("字段值一致，但配置版本已经变化。采用服务端版本后可继续编辑。")
                .foregroundStyle(QuantXTheme.secondaryText)
            } else {
              ForEach(conflict.differences) { difference in
                VStack(alignment: .leading, spacing: 8) {
                  HStack(alignment: .firstTextBaseline) {
                    Text(difference.title)
                      .font(.subheadline.weight(.semibold))
                    Spacer()
                    Text(difference.key)
                      .font(.caption2.monospaced())
                      .foregroundStyle(QuantXTheme.secondaryText)
                  }
                  LabeledContent("你的值") {
                    Text(display(difference.userValue, missing: "此版本无该字段"))
                      .foregroundStyle(QuantXTheme.warning)
                  }
                  LabeledContent("服务端值") {
                    Text(display(difference.serverValue, missing: "服务端已移除"))
                      .foregroundStyle(QuantXTheme.accent)
                  }
                }
                .padding(.vertical, 4)
                .accessibilityElement(children: .combine)
              }
            }
          }

          if conflict.allowlistChanged {
            Section {
              Label(
                "服务端参数 allowlist 已变化，旧草稿不能自动重放。请采用服务端值后重新编辑。",
                systemImage: "lock.trianglebadge.exclamationmark"
              )
              .font(.footnote)
              .foregroundStyle(QuantXTheme.warning)
            }
          }

          Section {
            Button("采用服务端值", role: .destructive) {
              showAdoptServerConfirmation = true
            }
            .frame(minHeight: 44)
            .disabled(workspace.operationInProgress)

            Button {
              Task {
                do {
                  try await workspace.resubmitParametersAfterConflict(for: instance)
                  dismiss()
                } catch {
                  // The workspace publishes a precise, non-success error state.
                }
              }
            } label: {
              if workspace.operationInProgress {
                ProgressView()
                  .frame(maxWidth: .infinity)
              } else {
                Text("基于 v\(conflict.serverVersion) 重新提交")
                  .frame(maxWidth: .infinity)
              }
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(
              !conflict.canResubmit || workspace.operationInProgress
                || workspace.parameterEditingUnavailableReason(for: instance) != nil
            )
          } footer: {
            Text("重新提交只发送仍有差异的 typed allowlist 字段，并使用上方服务端版本作为 expectedVersion。")
          }
        }
      } else {
        ContentUnavailableView {
          Label("差异已处理", systemImage: "checkmark.circle.fill")
        } description: {
          Text("返回策略详情可查看最新服务端参数。")
        }
      }
    }
    .navigationTitle("核对参数差异")
    .navigationBarTitleDisplayMode(.inline)
    .confirmationDialog(
      "采用服务端参数？",
      isPresented: $showAdoptServerConfirmation,
      titleVisibility: .visible
    ) {
      Button("采用服务端值并放弃草稿", role: .destructive) {
        workspace.adoptServerValuesAfterConflict()
        dismiss()
      }
      Button("继续核对", role: .cancel) {}
    } message: {
      Text("此操作只替换本机草稿，不会向服务端发送 Mutation。")
    }
  }

  private func display(
    _ value: StrategyMobileParameterValue?,
    missing: String
  ) -> String {
    value?.displayValue ?? "（\(missing)）"
  }
}

private struct StrategyControlConfirmationSheet: View {
  @Environment(\.dismiss) private var dismiss
  let preview: StrategyControlPreviewTicket
  @ObservedObject var workspace: StrategyWorkspace

  var body: some View {
    NavigationStack {
      Form {
        Section {
          Label {
            VStack(alignment: .leading, spacing: 4) {
              Text(preview.action.title)
                .font(.headline)
              Text("本机认证仅确认这一项生命周期动作，不代表委托或成交。")
                .font(.caption)
                .foregroundStyle(QuantXTheme.secondaryText)
            }
          } icon: {
            Image(systemName: "faceid")
              .foregroundStyle(QuantXTheme.warning)
          }
        }

        Section("绑定上下文") {
          LabeledContent("账户", value: maskedAccountID)
          LabeledContent("策略实例", value: preview.instanceID)
          if preview.targetInstanceID != preview.instanceID {
            LabeledContent("目标实盘实例", value: preview.targetInstanceID)
          }
          LabeledContent("配置版本", value: preview.configVersion)
          LabeledContent("当前状态", value: "\(preview.currentMode) · \(preview.currentStatus)")
          TimelineView(.periodic(from: .now, by: 1)) { context in
            LabeledContent("确认有效期", value: expiryText(at: context.date))
              .foregroundStyle(
                preview.isExpired(at: context.date) ? QuantXTheme.critical : .primary)
          }
        }

        Section("实盘就绪检查") {
          ForEach(preview.checks) { check in
            Label {
              VStack(alignment: .leading, spacing: 2) {
                Text(check.message)
                Text(check.code)
                  .font(.caption2.monospaced())
                  .foregroundStyle(QuantXTheme.secondaryText)
              }
            } icon: {
              Image(systemName: check.passed ? "checkmark.circle.fill" : "xmark.octagon.fill")
                .foregroundStyle(check.passed ? QuantXTheme.online : QuantXTheme.critical)
            }
          }
        }

        if !preview.warnings.isEmpty {
          Section("确认须知") {
            ForEach(Array(preview.warnings.enumerated()), id: \.offset) { _, warning in
              Label(warning, systemImage: "exclamationmark.triangle.fill")
                .font(.footnote)
            }
          }
        }
      }
      .navigationTitle("实盘策略确认")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .cancellationAction) {
          Button("取消") {
            workspace.dismissPendingControl()
            dismiss()
          }
          .disabled(workspace.operationInProgress)
        }
      }
      .safeAreaInset(edge: .bottom) {
        TimelineView(.periodic(from: .now, by: 1)) { context in
          VStack(spacing: 8) {
            Button {
              Task { try? await workspace.confirmLiveControl(preview) }
            } label: {
              if workspace.operationInProgress {
                ProgressView()
                  .frame(maxWidth: .infinity)
              } else {
                Label("Face ID / Touch ID 确认", systemImage: "faceid")
                  .frame(maxWidth: .infinity)
              }
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .tint(QuantXTheme.approvalAction)
            .disabled(workspace.operationInProgress || preview.isExpired(at: context.date))
            .accessibilityHint("本机认证成功后，服务端将原子消费一次性策略控制凭据")

            Text("安全快照、配置版本、账户、会话或策略状态变化都会使确认失效")
              .font(.caption2)
              .foregroundStyle(QuantXTheme.secondaryText)
          }
          .padding(.horizontal, 16)
          .padding(.vertical, 12)
          .background(.ultraThinMaterial)
        }
      }
      .interactiveDismissDisabled(workspace.operationInProgress)
    }
  }

  private var maskedAccountID: String {
    guard preview.accountID.count > 4 else { return preview.accountID }
    return "•••• \(preview.accountID.suffix(4))"
  }

  private func expiryText(at date: Date) -> String {
    let seconds = max(0, Int(preview.expiresAt.timeIntervalSince(date).rounded(.up)))
    return seconds == 0 ? "已过期" : "\(seconds) 秒"
  }
}
