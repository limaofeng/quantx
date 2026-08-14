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
    }
    .animation(
      reduceMotion ? nil : .easeOut(duration: 0.16),
      value: model.privacyShieldVisible
    )
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
