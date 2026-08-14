import SwiftUI

struct PrivacyShieldView: View {
  var body: some View {
    ZStack {
      Color(uiColor: .systemBackground)
        .ignoresSafeArea()

      VStack(spacing: 14) {
        Image(systemName: "lock.shield.fill")
          .font(.system(size: 42, weight: .semibold))
          .foregroundStyle(QuantXTheme.accent)
          .accessibilityHidden(true)

        Text("QuantX 已保护")
          .font(.title2.bold())

        Text("返回 App 后验证身份并刷新量化数据")
          .font(.subheadline)
          .foregroundStyle(QuantXTheme.secondaryText)
      }
    }
    .accessibilityElement(children: .combine)
    .accessibilityLabel("QuantX 隐私保护已启用")
  }
}
