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

      NotificationSettingsSection(store: model.notificationStore)

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

private struct NotificationSettingsSection: View {
  @ObservedObject var store: PushNotificationStore

  var body: some View {
    Section("通知") {
      LabeledContent("系统授权", value: store.authorizationStatus.title)

      authorizationAction

      LabeledContent("设备注册", value: registrationTitle)

      ForEach(PushNotificationCategory.allCases) { category in
        preferenceRow(category)
      }

      if store.preferenceUpdateInProgress {
        HStack(spacing: 10) {
          ProgressView()
          Text("正在保存到个人服务")
            .foregroundStyle(QuantXTheme.secondaryText)
        }
        .accessibilityElement(children: .combine)
      }

      if let message = store.preferenceErrorMessage {
        Label(message, systemImage: "exclamationmark.triangle.fill")
          .font(.footnote)
          .foregroundStyle(QuantXTheme.warning)
          .fixedSize(horizontal: false, vertical: true)
      }

      Text("通知只提示有状态变化；账户、证券、金额与交易事实会在解锁后重新查询。")
        .font(.footnote)
        .foregroundStyle(QuantXTheme.secondaryText)
        .fixedSize(horizontal: false, vertical: true)
    }
  }

  @ViewBuilder
  private var authorizationAction: some View {
    switch store.authorizationStatus {
    case .notDetermined:
      Button {
        Task { await store.enableNotifications() }
      } label: {
        if store.authorizationRequestInProgress {
          HStack(spacing: 10) {
            ProgressView()
            Text("正在请求系统授权")
          }
        } else {
          Label("启用通知", systemImage: "bell.badge.fill")
        }
      }
      .disabled(store.authorizationRequestInProgress)
      .accessibilityHint("此操作会显示 iOS 通知授权提示")
    case .denied:
      Button("前往系统设置开启") {
        Task { await store.openSystemNotificationSettings() }
      }
    case .unknown:
      Button("重新读取系统权限") {
        Task { await store.refreshSystemAuthorization() }
      }
    case .authorized, .provisional, .ephemeral:
      Label("普通提醒已启用", systemImage: "bell.fill")
        .foregroundStyle(QuantXTheme.online)
    }
  }

  @ViewBuilder
  private func preferenceRow(_ category: PushNotificationCategory) -> some View {
    if let enabled = store.preferences?[category] {
      Toggle(
        isOn: Binding(
          get: { store.preferences?[category] ?? enabled },
          set: { newValue in
            Task { await store.updatePreference(category, enabled: newValue) }
          }
        )
      ) {
        VStack(alignment: .leading, spacing: 3) {
          Text(category.title)
          Text(category.detail)
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        }
      }
      .disabled(store.preferenceUpdateInProgress)
    } else {
      VStack(alignment: .leading, spacing: 3) {
        HStack {
          Text(category.title)
          Spacer()
          Text("同步后显示")
            .font(.caption)
            .foregroundStyle(QuantXTheme.secondaryText)
        }
        Text(category.detail)
          .font(.caption)
          .foregroundStyle(QuantXTheme.secondaryText)
      }
      .accessibilityElement(children: .combine)
    }
  }

  private var registrationTitle: String {
    switch store.registrationState {
    case .idle: "等待同步"
    case .waitingForAuthorization: "等待系统授权"
    case .waitingForToken: "等待 Apple 设备标识"
    case .waitingForSession: "等待登录会话"
    case .registering: "正在同步"
    case .registered: "已绑定当前会话"
    case .unavailable(let message): message
    }
  }
}
