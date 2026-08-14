import SwiftUI

struct ServiceStatusView: View {
  @EnvironmentObject private var model: AppModel

  var body: some View {
    List {
      Section("连接") {
        if let configuration = model.configuration {
          LabeledContent("环境", value: configuration.environment.displayName)
          LabeledContent("服务主机", value: configuration.serviceHost)
          LabeledContent("账户数据", value: configuration.accountDataEnabled ? "已启用" : "已关闭")
          LabeledContent(
            "账户传输",
            value: configuration.usesInsecureAccountTransport ? "HTTP/WS（开发）" : "HTTPS/WSS"
          )
        }
      }

      Section("后端确认状态") {
        statusContent
      }

      Section {
        Text("状态只来自后端健康检查；未知或缺失不会被推断为安全、已连接或已成交。")
          .font(.footnote)
          .foregroundStyle(QuantXTheme.secondaryText)
      }
    }
    .navigationTitle("服务状态")
    .refreshable {
      await model.refreshHealth()
    }
  }

  @ViewBuilder
  private var statusContent: some View {
    switch model.serviceState {
    case .idle, .loading:
      HStack {
        ProgressView()
        Text("正在刷新")
      }
    case .loaded(let snapshot):
      LabeledContent("GraphQL", value: snapshot.apiType ?? "未知")
      LabeledContent("服务", value: snapshot.status)
      LabeledContent("实时能力", value: realtimeValue(snapshot.realtimeEnabled))
      LabeledContent("服务环境", value: snapshot.environment ?? snapshot.profile ?? "未知")
      if let version = snapshot.version {
        LabeledContent("版本", value: version)
      }
      if let miniQMT = snapshot.miniQMT {
        LabeledContent("miniQMT", value: miniQMT.connected ? "已连接" : "未连接")
        LabeledContent("账户连接", value: miniQMT.accountConnected ? "已连接" : "未连接")
        LabeledContent("连接状态", value: miniQMT.connectionState)
      } else if let qmtAgent = snapshot.components["qmtAgent"] {
        LabeledContent("QMT Agent", value: componentValue(qmtAgent))
        if let onlineDevices = qmtAgent.onlineDevices {
          LabeledContent("在线交易设备", value: "\(onlineDevices)")
        }
      } else {
        LabeledContent("QMT Agent", value: "未知")
      }
      ForEach(snapshot.components.keys.sorted(), id: \.self) { key in
        if key != "qmtAgent", let component = snapshot.components[key] {
          LabeledContent(componentTitle(key), value: componentValue(component))
        }
      }
      LabeledContent("最后更新") {
        Text(snapshot.fetchedAt, style: .time)
          .monospacedDigit()
      }
    case .failed(let message):
      Label(message, systemImage: "wifi.exclamationmark")
        .foregroundStyle(QuantXTheme.warning)
    }
  }

  private func realtimeValue(_ enabled: Bool?) -> String {
    guard let enabled else { return "未知" }
    return enabled ? "后端已启用" : "后端未启用"
  }

  private func componentValue(_ component: HealthSnapshot.ComponentStatus) -> String {
    component.isReady ? "正常" : component.status
  }

  private func componentTitle(_ key: String) -> String {
    switch key {
    case "api": "API"
    case "database": "数据库"
    case "engine": "策略引擎"
    case "marketData": "行情服务"
    case "prefect": "Prefect"
    case "worker": "任务 Worker"
    default: key
    }
  }
}
