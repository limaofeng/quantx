import SwiftUI

struct DataFreshnessView: View {
  let updatedAt: Date?

  var body: some View {
    TimelineView(.periodic(from: .now, by: 30)) { context in
      let freshness = DataFreshness.evaluate(updatedAt: updatedAt, now: context.date)
      Label {
        Text(label(for: freshness))
      } icon: {
        Image(systemName: systemImage(for: freshness.level))
      }
      .font(.caption.weight(.semibold))
      .foregroundStyle(color(for: freshness.level))
      .accessibilityElement(children: .combine)
    }
  }

  private func label(for freshness: DataFreshness) -> String {
    let age = PortfolioFormatters.relativeAge(freshness.age)
    switch freshness.level {
    case .current:
      return age
    case .delayed:
      return "数据可能延迟 · \(age)"
    case .stale:
      return "数据已过期 · \(age)"
    case .unknown:
      return "数据更新时间未知"
    }
  }

  private func systemImage(for level: DataFreshness.Level) -> String {
    switch level {
    case .current: "checkmark.circle.fill"
    case .delayed: "clock.badge.exclamationmark.fill"
    case .stale: "exclamationmark.triangle.fill"
    case .unknown: "questionmark.circle.fill"
    }
  }

  private func color(for level: DataFreshness.Level) -> Color {
    switch level {
    case .current: QuantXTheme.online
    case .delayed, .stale: QuantXTheme.warning
    case .unknown: QuantXTheme.secondaryText
    }
  }
}

struct RefreshWarningView: View {
  let message: String

  var body: some View {
    Label {
      Text(message)
        .fixedSize(horizontal: false, vertical: true)
    } icon: {
      Image(systemName: "arrow.clockwise.circle.fill")
        .accessibilityHidden(true)
    }
    .font(.footnote)
    .foregroundStyle(QuantXTheme.warning)
    .frame(maxWidth: .infinity, alignment: .leading)
    .padding(12)
    .background(QuantXTheme.warning.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
    .accessibilityElement(children: .combine)
    .accessibilityAddTraits(.isStaticText)
  }
}
