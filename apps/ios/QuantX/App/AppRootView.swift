import SwiftUI

struct AppRootView: View {
  @EnvironmentObject private var model: AppModel
  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  var body: some View {
    ZStack {
      if let errorMessage = model.configurationErrorMessage {
        ConfigurationFailureView(message: errorMessage)
      } else if model.requiresLocalUnlock {
        LocalUnlockView()
      } else if model.requiresAuthentication {
        LoginView()
      } else {
        TabView(selection: $model.selectedTab) {
          DashboardView()
            .tabItem {
              AppTabLabel(tab: .today)
            }
            .tag(AppTab.today)

          MarketWorkspaceView()
            .tabItem {
              AppTabLabel(tab: .market)
            }
            .tag(AppTab.market)

          TradeWorkspaceView()
            .tabItem {
              AppTabLabel(tab: .trade)
            }
            .tag(AppTab.trade)

          QuantWorkspaceView()
            .tabItem {
              AppTabLabel(tab: .quant)
            }
            .tag(AppTab.quant)

          PortfolioView()
            .tabItem {
              AppTabLabel(tab: .assets)
            }
            .tag(AppTab.assets)
        }
        .tint(QuantXTheme.accent)
      }

      if model.privacyShieldVisible {
        PrivacyShieldView()
          .transition(.opacity)
          .zIndex(10)
      }

      NotificationDeepLinkLayer(store: model.notificationStore) { request in
        await model.handleNotificationNavigation(request)
      }
      .zIndex(9)
    }
    .animation(
      reduceMotion ? nil : .easeOut(duration: 0.16),
      value: model.privacyShieldVisible
    )
  }
}

private struct NotificationDeepLinkLayer: View {
  @ObservedObject var store: PushNotificationStore
  let navigate: @MainActor (NotificationNavigationRequest) async -> Void

  var body: some View {
    ZStack(alignment: .top) {
      Color.clear
        .allowsHitTesting(false)
      if case .resolving = store.deepLinkState {
        HStack(spacing: 10) {
          ProgressView()
          Text("正在验证通知并刷新最新状态")
            .font(.subheadline.weight(.medium))
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(.regularMaterial, in: Capsule())
        .padding(.top, 12)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("正在验证通知并刷新最新状态")
        .allowsHitTesting(false)
      }
    }
    .task(id: store.navigationRequest?.id) {
      guard let request = store.consumeNavigationRequest() else { return }
      await navigate(request)
    }
    .alert(
      "通知不可用",
      isPresented: Binding(
        get: {
          if case .unavailable = store.deepLinkState { return true }
          return false
        },
        set: { isPresented in
          if !isPresented { store.dismissDeepLinkStatus() }
        }
      )
    ) {
      Button("知道了") { store.dismissDeepLinkStatus() }
    } message: {
      Text(deepLinkMessage)
    }
  }

  private var deepLinkMessage: String {
    if case .unavailable(let message) = store.deepLinkState {
      return message
    }
    return "未执行任何操作"
  }
}

private struct AppTabLabel: View {
  let tab: AppTab

  var body: some View {
    Label {
      Text(tab.title)
    } icon: {
      Image(systemName: tab.systemImage)
        .symbolRenderingMode(.monochrome)
    }
  }
}
