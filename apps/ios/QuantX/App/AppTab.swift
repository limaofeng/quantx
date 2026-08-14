import SwiftUI

enum AppTab: String, CaseIterable, Identifiable {
  case dashboard
  case portfolio
  case strategies
  case orders
  case settings

  var id: Self { self }

  var title: String {
    switch self {
    case .dashboard: "首页"
    case .portfolio: "持仓"
    case .strategies: "策略"
    case .orders: "委托成交"
    case .settings: "设置"
    }
  }

  var systemImage: String {
    switch self {
    case .dashboard: "chart.pie.fill"
    case .portfolio: "list.bullet.rectangle.portrait.fill"
    case .strategies: "waveform.path.ecg.rectangle.fill"
    case .orders: "doc.text.magnifyingglass"
    case .settings: "gearshape.fill"
    }
  }
}
