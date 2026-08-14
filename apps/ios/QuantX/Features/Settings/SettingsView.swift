import SwiftUI

struct SettingsView: View {
  @EnvironmentObject private var model: AppModel

  var body: some View {
    NavigationStack {
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
          Label("不包含交易写入能力", systemImage: "hand.raised.fill")
        }

        Section("产品边界") {
          Label("账户、持仓与交易事实只读展示", systemImage: "eye.fill")
          Label("不提供下单与撤单", systemImage: "arrow.left.arrow.right")
          Label("不提供策略控制与参数修改", systemImage: "slider.horizontal.3")
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
      .navigationTitle("设置")
    }
  }

  private var appVersion: String {
    let shortVersion =
      Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—"
    let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "—"
    return "\(shortVersion) (\(build))"
  }
}
