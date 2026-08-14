import SwiftUI

struct ConfigurationFailureView: View {
  let message: String

  var body: some View {
    ContentUnavailableView {
      Label("配置不可用", systemImage: "exclamationmark.triangle.fill")
    } description: {
      Text(message)
    }
    .foregroundStyle(.primary)
  }
}
