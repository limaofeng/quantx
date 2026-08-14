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
              Label(AppTab.today.title, systemImage: AppTab.today.systemImage)
            }
            .tag(AppTab.today)

          MarketWorkspaceView()
            .tabItem {
              Label(AppTab.market.title, systemImage: AppTab.market.systemImage)
            }
            .tag(AppTab.market)

          TradeWorkspaceView()
            .tabItem {
              Label(AppTab.trade.title, systemImage: AppTab.trade.systemImage)
            }
            .tag(AppTab.trade)

          QuantWorkspaceView()
            .tabItem {
              Label(AppTab.quant.title, systemImage: AppTab.quant.systemImage)
            }
            .tag(AppTab.quant)

          PortfolioView()
            .tabItem {
              Label(AppTab.assets.title, systemImage: AppTab.assets.systemImage)
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
