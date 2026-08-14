import SwiftUI

@main
struct QuantXApp: App {
  @Environment(\.scenePhase) private var scenePhase
  @StateObject private var model = AppModel()

  var body: some Scene {
    WindowGroup {
      rootContent
        .environmentObject(model)
        .task {
          await model.start()
        }
        .onChange(of: scenePhase) { _, newPhase in
          model.handleScenePhase(newPhase)
        }
    }
  }

  @ViewBuilder
  private var rootContent: some View {
#if DEBUG
    if ProcessInfo.processInfo.arguments.contains("-QuantXTradeApprovalUITesting") {
      TradeApprovalSheet(preview: .uiTestFixture) {}
    } else {
      AppRootView()
    }
#else
    AppRootView()
#endif
  }
}

#if DEBUG
private extension TradeApprovalPreview {
  static var uiTestFixture: Self {
    Self(
      id: "approval-preview-ui-test",
      confirmationToken: "secret-token-must-never-appear",
      kind: .strategyTradeIntent,
      accountID: "300000013250",
      runID: "limit-up-run-ui-test",
      intentID: "limit-up-intent-ui-test",
      instrumentCode: "600519.SH",
      side: "BUY",
      bucket: "活跃仓",
      reason: "涨停回封信号满足服务端门禁",
      targetVolume: 100,
      referencePrice: 1_598.50,
      estimatedAmount: 159_850,
      signalExpiresAt: Date().addingTimeInterval(120),
      challengeExpiresAt: Date().addingTimeInterval(55),
      warnings: [
        "价格变化超过风控阈值时会拒绝提交",
        "Kill Switch 或账户状态变化时会拒绝提交",
      ]
    )
  }
}
#endif
