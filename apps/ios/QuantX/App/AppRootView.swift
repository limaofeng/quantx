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
              Label(AppTab.dashboard.title, systemImage: AppTab.dashboard.systemImage)
            }
            .tag(AppTab.dashboard)

          PortfolioView()
            .tabItem {
              Label(AppTab.portfolio.title, systemImage: AppTab.portfolio.systemImage)
            }
            .tag(AppTab.portfolio)

          StrategiesView()
            .tabItem {
              Label(AppTab.strategies.title, systemImage: AppTab.strategies.systemImage)
            }
            .tag(AppTab.strategies)

          TradingActivityView()
            .tabItem {
              Label(AppTab.orders.title, systemImage: AppTab.orders.systemImage)
            }
            .tag(AppTab.orders)

          SettingsView()
            .tabItem {
              Label(AppTab.settings.title, systemImage: AppTab.settings.systemImage)
            }
            .tag(AppTab.settings)
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
