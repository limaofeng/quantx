import SwiftUI

enum QuantXTheme {
  enum Spacing {
    static let xSmall: CGFloat = 4
    static let small: CGFloat = 8
    static let medium: CGFloat = 12
    static let large: CGFloat = 16
    static let xLarge: CGFloat = 24
  }

  enum Radius {
    static let compact: CGFloat = 10
    static let control: CGFloat = 12
    static let card: CGFloat = 16
    static let hero: CGFloat = 22
  }

  static let accent = adaptiveColor(
    light: UIColor(red: 36 / 255, green: 87 / 255, blue: 214 / 255, alpha: 1),
    dark: UIColor(red: 111 / 255, green: 149 / 255, blue: 255 / 255, alpha: 1)
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
  static let critical = adaptiveColor(
    light: UIColor(red: 180 / 255, green: 35 / 255, blue: 24 / 255, alpha: 1),
    dark: UIColor(red: 255 / 255, green: 125 / 255, blue: 115 / 255, alpha: 1)
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
  static let elevatedBackground = Color(uiColor: .tertiarySystemGroupedBackground)
  static let separator = Color(uiColor: .separator)

  static func trendColor(_ value: Double?) -> Color {
    guard let value else { return secondaryText }
    if value > 0 { return positive }
    if value < 0 { return negative }
    return secondaryText
  }

  private static func adaptiveColor(light: UIColor, dark: UIColor) -> Color {
    Color(
      uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark ? dark : light
      }
    )
  }
}

struct QuantXCard<Content: View>: View {
  let contentPadding: CGFloat
  @ViewBuilder let content: Content

  init(
    contentPadding: CGFloat = QuantXTheme.Spacing.large,
    @ViewBuilder content: () -> Content
  ) {
    self.contentPadding = contentPadding
    self.content = content()
  }

  var body: some View {
    content
      .frame(maxWidth: .infinity, alignment: .leading)
      .padding(contentPadding)
      .background(
        QuantXTheme.cardBackground,
        in: RoundedRectangle(cornerRadius: QuantXTheme.Radius.card)
      )
      .overlay {
        RoundedRectangle(cornerRadius: QuantXTheme.Radius.card)
          .stroke(QuantXTheme.separator.opacity(0.16), lineWidth: 0.5)
      }
  }
}

enum QuantXSemanticStatus: Sendable {
  case ready
  case working
  case attention
  case blocked
  case unavailable

  var title: String {
    switch self {
    case .ready: "正常"
    case .working: "处理中"
    case .attention: "需关注"
    case .blocked: "已阻止"
    case .unavailable: "不可用"
    }
  }

  var systemImage: String {
    switch self {
    case .ready: "checkmark.circle.fill"
    case .working: "clock.arrow.circlepath"
    case .attention: "exclamationmark.triangle.fill"
    case .blocked: "hand.raised.fill"
    case .unavailable: "slash.circle.fill"
    }
  }

  var color: Color {
    switch self {
    case .ready: QuantXTheme.online
    case .working: QuantXTheme.accent
    case .attention: QuantXTheme.warning
    case .blocked: QuantXTheme.critical
    case .unavailable: QuantXTheme.secondaryText
    }
  }
}

struct QuantXStatusBanner: View {
  let title: String
  let message: String
  let status: QuantXSemanticStatus

  var body: some View {
    HStack(alignment: .top, spacing: QuantXTheme.Spacing.medium) {
      Image(systemName: status.systemImage)
        .font(.body.weight(.semibold))
        .foregroundStyle(status.color)
        .frame(width: 32, height: 32)
        .background(status.color.opacity(0.12), in: Circle())
        .accessibilityHidden(true)

      VStack(alignment: .leading, spacing: QuantXTheme.Spacing.xSmall) {
        Text(title)
          .font(.subheadline.weight(.semibold))
        Text(message)
          .font(.caption)
          .foregroundStyle(QuantXTheme.secondaryText)
          .fixedSize(horizontal: false, vertical: true)
      }
      Spacer(minLength: 0)
    }
    .padding(QuantXTheme.Spacing.medium)
    .background(status.color.opacity(0.08), in: RoundedRectangle(cornerRadius: 14))
    .overlay {
      RoundedRectangle(cornerRadius: 14)
        .stroke(status.color.opacity(0.20), lineWidth: 1)
    }
    .accessibilityElement(children: .combine)
  }
}

struct QuantXMetricTile: View {
  let title: String
  let value: String
  var detail: String?
  var trend: Double?

  var body: some View {
    VStack(alignment: .leading, spacing: QuantXTheme.Spacing.xSmall) {
      Text(title)
        .font(.caption)
        .foregroundStyle(QuantXTheme.secondaryText)
      Text(value)
        .font(.headline)
        .monospacedDigit()
        .foregroundStyle(trend == nil ? Color.primary : QuantXTheme.trendColor(trend))
        .minimumScaleFactor(0.72)
      if let detail {
        Text(detail)
          .font(.caption2)
          .monospacedDigit()
          .foregroundStyle(trend == nil ? QuantXTheme.secondaryText : QuantXTheme.trendColor(trend))
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .padding(QuantXTheme.Spacing.medium)
    .background(QuantXTheme.elevatedBackground, in: RoundedRectangle(cornerRadius: 12))
    .accessibilityElement(children: .combine)
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
