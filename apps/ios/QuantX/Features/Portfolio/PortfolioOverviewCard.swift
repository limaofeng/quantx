import SwiftUI

struct PortfolioOverviewCard: View {
  let snapshot: PortfolioSnapshot

  private let columns = [
    GridItem(.adaptive(minimum: 132), spacing: 12, alignment: .top)
  ]

  var body: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: 16) {
        HStack(alignment: .firstTextBaseline) {
          VStack(alignment: .leading, spacing: 4) {
            Text(snapshot.account.name)
              .font(.subheadline.weight(.semibold))
              .foregroundStyle(QuantXTheme.secondaryText)
            Text("总资产")
              .font(.caption)
              .foregroundStyle(QuantXTheme.secondaryText)
          }
          Spacer(minLength: 8)
          DataFreshnessView(updatedAt: snapshot.sourceUpdatedAt)
        }

        Text(PortfolioFormatters.currency(snapshot.metrics.totalAsset))
          .font(.title.bold())
          .monospacedDigit()
          .contentTransition(.numericText())
          .accessibilityLabel("总资产 \(PortfolioFormatters.currency(snapshot.metrics.totalAsset))")

        LazyVGrid(columns: columns, alignment: .leading, spacing: 14) {
          FinancialMetricView(
            title: "可用资金",
            value: PortfolioFormatters.currency(snapshot.metrics.cash)
          )
          FinancialMetricView(
            title: "当日盈亏",
            value: PortfolioFormatters.currency(snapshot.metrics.todayProfitLoss),
            trend: snapshot.metrics.todayProfitLoss
          )
          FinancialMetricView(
            title: "持仓市值",
            value: PortfolioFormatters.currency(snapshot.metrics.marketValue)
          )
          FinancialMetricView(
            title: "总盈亏",
            value: PortfolioFormatters.currency(snapshot.metrics.totalProfitLoss),
            trend: snapshot.metrics.totalProfitLoss,
            detail: PortfolioFormatters.signedPercentage(
              snapshot.metrics.totalProfitLossPercent
            )
          )
        }

        if snapshot.positionCountDoesNotMatch {
          Label(
            "组合汇总与持仓列表来自不同快照，数量暂不一致",
            systemImage: "exclamationmark.triangle.fill"
          )
          .font(.caption)
          .foregroundStyle(QuantXTheme.warning)
          .accessibilityElement(children: .combine)
        }
      }
    }
  }
}

struct FinancialMetricView: View {
  let title: String
  let value: String
  var trend: Double?
  var detail: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 4) {
      Text(title)
        .font(.caption)
        .foregroundStyle(QuantXTheme.secondaryText)
      HStack(spacing: 5) {
        if let trend {
          Image(systemName: trendImage(for: trend))
            .font(.caption.weight(.bold))
            .accessibilityHidden(true)
        }
        Text(value)
          .font(.subheadline.weight(.semibold))
          .monospacedDigit()
      }
      .foregroundStyle(trendColor(for: trend))
      if let detail {
        Text(detail)
          .font(.caption)
          .monospacedDigit()
          .foregroundStyle(trendColor(for: trend))
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .accessibilityElement(children: .combine)
    .accessibilityLabel(accessibilityDescription)
  }

  private var accessibilityDescription: String {
    let direction: String
    if let trend {
      direction = trend > 0 ? "盈利" : trend < 0 ? "亏损" : "持平"
    } else {
      direction = ""
    }
    return [title, direction, value, detail].compactMap { $0 }.joined(separator: " ")
  }

  private func trendColor(for value: Double?) -> Color {
    guard let value else { return .primary }
    if value > 0 { return QuantXTheme.positive }
    if value < 0 { return QuantXTheme.negative }
    return QuantXTheme.secondaryText
  }

  private func trendImage(for value: Double) -> String {
    if value > 0 { return "arrow.up.right" }
    if value < 0 { return "arrow.down.right" }
    return "minus"
  }
}

struct PortfolioPositionRow: View {
  let position: PortfolioPosition

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack(alignment: .firstTextBaseline) {
        VStack(alignment: .leading, spacing: 3) {
          Text(position.displayName)
            .font(.headline)
            .foregroundStyle(.primary)
          Text(position.stockCode)
            .font(.caption)
            .monospacedDigit()
            .foregroundStyle(QuantXTheme.secondaryText)
        }
        Spacer(minLength: 12)
        VStack(alignment: .trailing, spacing: 3) {
          TrendLabel(
            value: PortfolioFormatters.currency(position.profitLoss),
            percentage: PortfolioFormatters.signedPercentage(position.profitRate),
            trend: position.profitLoss
          )
        }
      }

      HStack {
        compactMetric("市值", PortfolioFormatters.currency(position.marketValue))
        Spacer(minLength: 10)
        compactMetric(
          "持仓 / 可用",
          "\(PortfolioFormatters.integer(position.volume)) / \(PortfolioFormatters.integer(position.availableVolume))"
        )
      }
    }
    .padding(.vertical, 4)
    .contentShape(Rectangle())
    .accessibilityElement(children: .combine)
    .accessibilityLabel(
      "\(position.displayName)，代码 \(position.stockCode)，市值 \(PortfolioFormatters.currency(position.marketValue))，盈亏 \(PortfolioFormatters.currency(position.profitLoss))，\(PortfolioFormatters.signedPercentage(position.profitRate))"
    )
  }

  private func compactMetric(_ title: String, _ value: String) -> some View {
    VStack(alignment: .leading, spacing: 2) {
      Text(title)
        .font(.caption2)
        .foregroundStyle(QuantXTheme.secondaryText)
      Text(value)
        .font(.caption.weight(.medium))
        .monospacedDigit()
        .foregroundStyle(.primary)
    }
  }
}

struct TrendLabel: View {
  let value: String
  let percentage: String
  let trend: Double?

  var body: some View {
    HStack(spacing: 4) {
      Image(systemName: systemImage)
        .font(.caption.weight(.bold))
        .accessibilityHidden(true)
      VStack(alignment: .trailing, spacing: 2) {
        Text(value)
          .font(.subheadline.weight(.semibold))
        Text(percentage)
          .font(.caption)
      }
      .monospacedDigit()
    }
    .foregroundStyle(color)
  }

  private var color: Color {
    guard let trend else { return QuantXTheme.secondaryText }
    if trend > 0 { return QuantXTheme.positive }
    if trend < 0 { return QuantXTheme.negative }
    return QuantXTheme.secondaryText
  }

  private var systemImage: String {
    guard let trend else { return "questionmark" }
    if trend > 0 { return "arrow.up.right" }
    if trend < 0 { return "arrow.down.right" }
    return "minus"
  }
}
