import SwiftUI

struct SettingsView: View {
  @EnvironmentObject private var model: AppModel
  var embeddedInNavigation = false

  @ViewBuilder
  var body: some View {
    if embeddedInNavigation {
      content
    } else {
      NavigationStack {
        content
      }
    }
  }

  private var content: some View {
    List {
        Section("环境") {
          if let configuration = model.configuration {
            LabeledContent("当前环境", value: configuration.environment.displayName)
            LabeledContent("服务主机", value: configuration.serviceHost)
            LabeledContent("账户查询", value: configuration.accountDataEnabled ? "已启用" : "已关闭")
            LabeledContent(
              "账户传输",
              value: configuration.usesInsecureAccountTransport ? "HTTP/WS（开发）" : "HTTPS/WSS"
            )
          }
        }

        Section("安全") {
          Label("Token 仅存 Keychain", systemImage: "key.fill")
          Label("切入后台自动遮蔽", systemImage: "rectangle.inset.filled.and.person.filled")
          Label("实盘操作使用预览与生物识别", systemImage: "faceid")
        }

        Section("个人量化边界") {
          Label("iOS 只连接 QuantX 私有服务", systemImage: "network.badge.shield.half.filled")
          Label("不直接访问 QMT 或券商凭证", systemImage: "building.columns.fill")
          Label("成交只认券商回报收敛结果", systemImage: "checkmark.seal.fill")
        }

        if let user = model.authenticatedUser {
          Section("会话") {
            LabeledContent("用户", value: user.displayName)
            LabeledContent("会话权限", value: "\(user.permissions.count) 项")
            Button("退出当前设备", role: .destructive) {
              Task { await model.logout() }
            }
            Button("退出全部设备", role: .destructive) {
              Task { await model.logout(allDevices: true) }
            }
          }
        }

        Section("版本") {
          LabeledContent("App", value: appVersion)
          LabeledContent("最低系统", value: "iOS 17")
        }
    }
    .navigationTitle("账户与设置")
  }

  private var appVersion: String {
    let shortVersion =
      Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—"
    let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "—"
    return "\(shortVersion) (\(build))"
  }
}
