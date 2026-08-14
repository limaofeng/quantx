import SwiftUI

struct FeatureLockedView: View {
  let feature: AppTab

  var body: some View {
    NavigationStack {
      ContentUnavailableView {
        Label(feature.title, systemImage: "lock.shield.fill")
      } description: {
        Text("后端完成统一认证、账户归属与对应作用域权限验收后启用。")
      }
      .navigationTitle(feature.title)
    }
  }
}
