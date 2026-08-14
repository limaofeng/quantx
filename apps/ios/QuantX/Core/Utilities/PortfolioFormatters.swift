import Foundation

enum PortfolioFormatters {
  static func currency(_ value: Double?) -> String {
    guard let value, value.isFinite else { return "—" }
    let formatter = NumberFormatter()
    formatter.locale = Locale(identifier: "zh_CN")
    formatter.numberStyle = .currency
    formatter.currencyCode = "CNY"
    formatter.currencySymbol = "¥"
    formatter.minimumFractionDigits = 2
    formatter.maximumFractionDigits = 2
    return formatter.string(from: NSNumber(value: value)) ?? "—"
  }

  static func decimal(_ value: Double?, fractionDigits: Int = 2) -> String {
    guard let value, value.isFinite else { return "—" }
    let formatter = NumberFormatter()
    formatter.locale = Locale(identifier: "zh_CN")
    formatter.numberStyle = .decimal
    formatter.minimumFractionDigits = fractionDigits
    formatter.maximumFractionDigits = fractionDigits
    return formatter.string(from: NSNumber(value: value)) ?? "—"
  }

  static func integer(_ value: Int?) -> String {
    guard let value else { return "—" }
    return value.formatted(.number.locale(Locale(identifier: "zh_CN")))
  }

  static func signedPercentage(_ value: Double?) -> String {
    guard let value, value.isFinite else { return "—" }
    let sign = value > 0 ? "+" : ""
    return "\(sign)\(decimal(value))%"
  }

  static func percentage(_ value: Double?) -> String {
    guard let value, value.isFinite else { return "—" }
    return "\(decimal(value))%"
  }

  static func relativeAge(_ age: TimeInterval?) -> String {
    guard let age else { return "更新时间未知" }
    switch age {
    case ..<60:
      return "刚刚更新"
    case ..<3_600:
      return "\(Int(age / 60)) 分钟前更新"
    case ..<86_400:
      return "\(Int(age / 3_600)) 小时前更新"
    default:
      return "\(Int(age / 86_400)) 天前更新"
    }
  }
}
