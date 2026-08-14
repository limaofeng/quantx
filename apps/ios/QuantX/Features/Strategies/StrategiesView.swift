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
        StrategyMonitorDetailView(instance: instance)
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
            Text("只读状态，不提供启动、暂停或参数修改")
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
            .accessibilityHint("查看策略只读详情")
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
  let instance: StrategyMonitorItem

  var body: some View {
    List {
      Section("实例") {
        LabeledContent("名称", value: instance.displayName)
        LabeledContent("策略", value: instance.strategyName ?? instance.strategyKey)
        LabeledContent("标的", value: instance.instrumentCode)
        LabeledContent("模式", value: instance.modeDisplayName)
        LabeledContent("状态", value: instance.statusDisplayName)
      }

      Section("运行事实") {
        LabeledContent("参数版本", value: instance.parameterVersion)
        LabeledContent("最近执行", value: instance.latestExecutionStatus ?? "暂无")
        LabeledContent("最近决策") {
          Text(instance.lastDecisionAt?.formatted(date: .abbreviated, time: .standard) ?? "暂无")
            .monospacedDigit()
        }
        LabeledContent("更新时间") {
          Text(instance.updatedAt.formatted(date: .abbreviated, time: .standard))
            .monospacedDigit()
        }
      }

      Section("安全边界") {
        Text("本页只展示服务端策略快照，不提供启动、暂停、参数修改或实盘模式切换。")
          .font(.footnote)
          .foregroundStyle(QuantXTheme.secondaryText)
      }
    }
    .navigationTitle(instance.displayName)
    .navigationBarTitleDisplayMode(.inline)
  }
}
