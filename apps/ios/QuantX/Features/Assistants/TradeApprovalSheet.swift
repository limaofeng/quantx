import SwiftUI

struct TradeApprovalSheet: View {
  @Environment(\.dismiss) private var dismiss
  @EnvironmentObject private var model: AppModel

  let preview: TradeApprovalPreview
  let onFinished: () -> Void

  @State private var errorMessage: String?
  @State private var confirmation: TradeApprovalConfirmation?

  var body: some View {
    NavigationStack {
      ScrollView {
        VStack(alignment: .leading, spacing: 16) {
          header
          tradeDetails
          warnings
          outcome
        }
        .padding(16)
      }
      .background(QuantXTheme.canvasBackground)
      .navigationTitle("安全交易确认")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .cancellationAction) {
          Button("取消") { dismiss() }
            .disabled(model.tradeApprovalInProgress)
        }
      }
      .safeAreaInset(edge: .bottom) {
        actionBar
      }
      .interactiveDismissDisabled(model.tradeApprovalInProgress)
    }
  }

  private var header: some View {
    QuantXCard {
      HStack(alignment: .top, spacing: 13) {
        Image(systemName: "faceid")
          .font(.title)
          .foregroundStyle(QuantXTheme.warning)
          .frame(width: 48, height: 48)
          .background(
            QuantXTheme.warning.opacity(0.14),
            in: RoundedRectangle(cornerRadius: 14)
          )
        VStack(alignment: .leading, spacing: 5) {
          Text("核对后使用生物识别")
            .font(.headline)
          Text("确认只授权这一笔意图进入统一下单风控，不代表已经委托或成交。")
            .font(.subheadline)
            .foregroundStyle(QuantXTheme.secondaryText)
            .fixedSize(horizontal: false, vertical: true)
        }
      }
    }
  }

  private var tradeDetails: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: 12) {
        HStack(alignment: .firstTextBaseline) {
          VStack(alignment: .leading, spacing: 3) {
            Text(preview.instrumentCode)
              .font(.title3.weight(.bold).monospaced())
              .lineLimit(1)
              .minimumScaleFactor(0.62)
              .allowsTightening(true)
              .layoutPriority(1)
              .accessibilityLabel(accessibleInstrumentCode)
            Text(preview.reason)
              .font(.caption)
              .foregroundStyle(QuantXTheme.secondaryText)
              .fixedSize(horizontal: false, vertical: true)
          }
          Spacer(minLength: 12)
          Text("买入")
            .font(.subheadline.weight(.bold))
            .foregroundStyle(QuantXTheme.warning)
        }

        Divider()
        detailRow(
          "资金账户",
          maskedAccountID,
          accessibilityValue: "尾号 \(spokenDigits(String(preview.accountID.suffix(4))))"
        )
        detailRow("仓位归属", preview.bucket.isEmpty ? "—" : preview.bucket)
        detailRow(
          "目标数量",
          preview.targetVolume.map { "\($0.formatted()) 股" } ?? "由交易域计算",
          accessibilityValue: preview.targetVolume.map {
            "\(spokenNumber(Double($0))) 股"
          } ?? "由交易域计算"
        )
        detailRow(
          "参考价格",
          PortfolioFormatters.decimal(preview.referencePrice),
          accessibilityValue: preview.referencePrice.map {
            "\(spokenNumber($0)) 元每股"
          } ?? "未提供"
        )
        detailRow(
          "预估金额",
          PortfolioFormatters.currency(preview.estimatedAmount),
          accessibilityValue: preview.estimatedAmount.map {
            "\(spokenNumber($0)) 元"
          } ?? "未提供"
        )

        TimelineView(.periodic(from: .now, by: 1)) { context in
          HStack {
            Text("确认凭据")
              .foregroundStyle(QuantXTheme.secondaryText)
            Spacer()
            Text(expiryText(at: context.date))
              .fontWeight(.semibold)
              .monospacedDigit()
              .lineLimit(1)
              .minimumScaleFactor(0.62)
              .allowsTightening(true)
              .layoutPriority(1)
              .foregroundStyle(preview.isExpired(at: context.date) ? QuantXTheme.warning : QuantXTheme.accent)
              .accessibilityLabel(expiryAccessibilityLabel(at: context.date))
          }
          .font(.subheadline)
        }
      }
    }
  }

  private var warnings: some View {
    QuantXCard {
      VStack(alignment: .leading, spacing: 10) {
        Label("提交前仍会重新风控", systemImage: "shield.lefthalf.filled")
          .font(.headline)
        ForEach(Array(preview.warnings.enumerated()), id: \.offset) { _, warning in
          Label {
            Text(warning)
              .fixedSize(horizontal: false, vertical: true)
          } icon: {
            Image(systemName: "checkmark.circle.fill")
              .foregroundStyle(QuantXTheme.online)
          }
          .font(.caption)
        }
      }
    }
  }

  @ViewBuilder
  private var outcome: some View {
    if let confirmation {
      QuantXCard {
        Label {
          VStack(alignment: .leading, spacing: 4) {
            Text("已进入统一执行链路")
              .font(.headline)
            Text("\(confirmation.message)（\(confirmation.code)）")
              .font(.caption)
              .foregroundStyle(QuantXTheme.secondaryText)
          }
        } icon: {
          Image(systemName: "checkmark.shield.fill")
            .foregroundStyle(QuantXTheme.online)
        }
      }
    } else if let errorMessage {
      QuantXCard {
        Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
          .font(.caption)
          .foregroundStyle(QuantXTheme.warning)
      }
    }
  }

  private var actionBar: some View {
    VStack(spacing: 8) {
      if confirmation == nil {
        TimelineView(.periodic(from: .now, by: 1)) { context in
          Button {
            Task { await confirm() }
          } label: {
            if model.tradeApprovalInProgress {
              ProgressView()
                .frame(maxWidth: .infinity)
            } else {
              Label("Face ID / Touch ID 确认", systemImage: "faceid")
                .frame(maxWidth: .infinity)
            }
          }
          .buttonStyle(.borderedProminent)
          .controlSize(.large)
          .tint(QuantXTheme.approvalAction)
          .disabled(model.tradeApprovalInProgress || preview.isExpired(at: context.date))
          .accessibilityHint("本机认证成功后，服务器会原子消费一次性确认凭据")
        }
      } else {
        Button("完成") {
          onFinished()
          dismiss()
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .frame(maxWidth: .infinity)
      }
      Text("不要在他人指示下确认；成交结果请以券商回报为准")
        .font(.caption2)
        .foregroundStyle(QuantXTheme.secondaryText)
    }
    .padding(.horizontal, 16)
    .padding(.vertical, 12)
    .background(.ultraThinMaterial)
  }

  private func detailRow(
    _ title: String,
    _ value: String,
    accessibilityValue: String? = nil
  ) -> some View {
    HStack(alignment: .firstTextBaseline) {
      Text(title)
        .foregroundStyle(QuantXTheme.secondaryText)
      Spacer(minLength: 16)
      Text(value)
        .fontWeight(.semibold)
        .multilineTextAlignment(.trailing)
        .monospacedDigit()
        .accessibilityLabel("\(title)，\(accessibilityValue ?? value)")
        .accessibilityIdentifier("trade-approval-\(title)")
    }
    .font(.subheadline)
  }

  private var maskedAccountID: String {
    guard preview.accountID.count > 4 else { return preview.accountID }
    return "•••• \(preview.accountID.suffix(4))"
  }

  private var accessibleInstrumentCode: String {
    let parts = preview.instrumentCode.split(separator: ".", maxSplits: 1)
    let code = spokenDigits(String(parts.first ?? ""))
    let exchange: String
    switch parts.count > 1 ? parts[1].uppercased() : "" {
    case "SH":
      exchange = "上海证券交易所"
    case "SZ":
      exchange = "深圳证券交易所"
    case "BJ":
      exchange = "北京证券交易所"
    default:
      exchange = "交易所未知"
    }
    return "证券代码 \(code)，\(exchange)"
  }

  private func spokenDigits(_ value: String) -> String {
    let names: [Character: String] = [
      "0": "零", "1": "一", "2": "二", "3": "三", "4": "四",
      "5": "五", "6": "六", "7": "七", "8": "八", "9": "九",
    ]
    return value.map { names[$0] ?? String($0) }.joined(separator: " ")
  }

  private func spokenNumber(_ value: Double) -> String {
    let formatter = NumberFormatter()
    formatter.locale = Locale(identifier: "zh_CN")
    formatter.numberStyle = .spellOut
    formatter.maximumFractionDigits = 2
    return formatter.string(from: NSNumber(value: value)) ?? value.formatted()
  }

  private func expiryText(at date: Date) -> String {
    let seconds = max(0, Int(preview.challengeExpiresAt.timeIntervalSince(date).rounded(.up)))
    return seconds > 0 ? "\(seconds) 秒后过期" : "已过期"
  }

  private func expiryAccessibilityLabel(at date: Date) -> String {
    let seconds = max(0, Int(preview.challengeExpiresAt.timeIntervalSince(date).rounded(.up)))
    return seconds > 0
      ? "确认凭据剩余 \(spokenNumber(Double(seconds))) 秒"
      : "确认凭据已过期"
  }

  private func confirm() async {
    errorMessage = nil
    do {
      confirmation = try await model.confirmTradeApproval(preview)
    } catch is CancellationError {
      return
    } catch {
      errorMessage = (error as? LocalizedError)?.errorDescription ?? "交易确认失败，请刷新后重试"
    }
  }
}
