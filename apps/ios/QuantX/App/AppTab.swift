import SwiftUI

enum AppTab: String, CaseIterable, Identifiable {
  case today
  case market
  case trade
  case quant
  case assets

  var id: Self { self }

  var title: String {
    switch self {
    case .today: "今日"
    case .market: "行情"
    case .trade: "交易"
    case .quant: "量化"
    case .assets: "资产"
    }
  }

  var systemImage: String {
    switch self {
    case .today: "sparkles.rectangle.stack.fill"
    case .market: "chart.xyaxis.line"
    case .trade: "arrow.left.arrow.right.circle.fill"
    case .quant: "waveform.path.ecg.rectangle.fill"
    case .assets: "chart.pie.fill"
    }
  }
}
