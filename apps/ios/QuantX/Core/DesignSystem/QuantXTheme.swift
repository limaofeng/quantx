import SwiftUI

enum QuantXTheme {
  static let accent = adaptiveColor(
    light: UIColor(red: 37 / 255, green: 99 / 255, blue: 235 / 255, alpha: 1),
    dark: UIColor(red: 110 / 255, green: 168 / 255, blue: 255 / 255, alpha: 1)
  )
  static let positive = adaptiveColor(
    light: UIColor(red: 201 / 255, green: 42 / 255, blue: 42 / 255, alpha: 1),
    dark: UIColor(red: 255 / 255, green: 107 / 255, blue: 107 / 255, alpha: 1)
  )
  static let negative = adaptiveColor(
    light: UIColor(red: 20 / 255, green: 122 / 255, blue: 63 / 255, alpha: 1),
    dark: UIColor(red: 74 / 255, green: 222 / 255, blue: 128 / 255, alpha: 1)
  )
  static let warning = adaptiveColor(
    light: UIColor(red: 154 / 255, green: 91 / 255, blue: 0 / 255, alpha: 1),
    dark: UIColor(red: 255 / 255, green: 193 / 255, blue: 90 / 255, alpha: 1)
  )
  static let approvalAction = adaptiveColor(
    light: UIColor(red: 138 / 255, green: 75 / 255, blue: 0 / 255, alpha: 1),
    dark: UIColor(red: 138 / 255, green: 75 / 255, blue: 0 / 255, alpha: 1)
  )
  static let online = negative
  static let secondaryText = Color(
    uiColor: UIColor { traits in
      traits.userInterfaceStyle == .dark
        ? UIColor(white: 0.72, alpha: 1)
        : UIColor(white: 0.32, alpha: 1)
    }
  )

  static let cardBackground = Color(uiColor: .secondarySystemGroupedBackground)
  static let canvasBackground = Color(uiColor: .systemGroupedBackground)

  private static func adaptiveColor(light: UIColor, dark: UIColor) -> Color {
    Color(
      uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark ? dark : light
      }
    )
  }
}

struct QuantXCard<Content: View>: View {
  @ViewBuilder let content: Content

  var body: some View {
    content
      .frame(maxWidth: .infinity, alignment: .leading)
      .padding(16)
      .background(QuantXTheme.cardBackground, in: RoundedRectangle(cornerRadius: 18))
  }
}

struct StatusBadge: View {
  @Environment(\.dynamicTypeSize) private var dynamicTypeSize

  let title: String
  let systemImage: String
  let color: Color

  var body: some View {
    Label(title, systemImage: systemImage)
      .font(.caption.weight(.semibold))
      .foregroundStyle(color)
      .padding(.horizontal, 10)
      .padding(.vertical, 6)
      .background(
        color.opacity(0.12),
        in: RoundedRectangle(cornerRadius: dynamicTypeSize.isAccessibilitySize ? 12 : 16)
      )
      .fixedSize(horizontal: false, vertical: true)
      .accessibilityLabel(title)
  }
}
