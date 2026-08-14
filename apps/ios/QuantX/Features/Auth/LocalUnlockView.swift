import SwiftUI

struct LocalUnlockView: View {
  @EnvironmentObject private var model: AppModel

  var body: some View {
    ZStack {
      QuantXTheme.canvasBackground
        .ignoresSafeArea()

      VStack(spacing: 18) {
        Image(systemName: "faceid")
          .font(.system(size: 46))
          .foregroundStyle(QuantXTheme.accent)
          .accessibilityHidden(true)
        Text("QuantX 已锁定")
          .font(.title2.bold())
        Text("验证设备身份后恢复个人量化会话。")
          .font(.subheadline)
          .foregroundStyle(QuantXTheme.secondaryText)
          .multilineTextAlignment(.center)

        if let message = model.localUnlockErrorMessage {
          Text(message)
            .font(.footnote)
            .foregroundStyle(QuantXTheme.warning)
            .multilineTextAlignment(.center)
        }

        Button("解锁") {
          Task { await model.unlockLocalSession() }
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
      }
      .padding(32)
    }
  }
}
