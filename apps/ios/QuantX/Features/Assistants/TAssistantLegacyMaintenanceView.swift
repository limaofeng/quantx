import SwiftUI

struct TAssistantLegacyMaintenanceView: View {
  @ObservedObject var store: TTradeControlStore
  @State private var reviewed = false
  @State private var windowStart = Date()
  @State private var windowEnd = Date().addingTimeInterval(600)

  var body: some View {
    Form {
      Section("本次维护") {
        Text("停止旧版做 T 的新买入，取消尚未路由且无成交线索的买入意图。已有订单与退出计划继续由原执行处理。")
        if let scope = store.legacyScope ?? store.legacySource?.scope {
          LabeledContent("账户", value: TTradeControlPrivacy.maskedAccount(scope.accountID))
          LabeledContent("复核版本", value: String(scope.headVersion))
        }
        if let error = store.errorMessage { Text(error).foregroundStyle(QuantXTheme.critical) }
        if let message = store.successMessage { Text(message) }
      }
      if store.legacyPreparationID == nil {
        Section("当前旧执行") {
          if let source = store.legacySource {
            if source.draining {
              Text("旧执行已停止接收新买入，原有订单与退出义务仍需继续核对。")
            } else {
              Button("准备义务清单") {
                Task {
                  do {
                    try await store.prepareLegacyInventory(source.scope)
                    try await store.refreshLegacyStatus()
                  } catch {}
                }
              }
              .disabled(store.operationInProgress)
            }
          } else if store.legacySourceLoaded {
            Text("当前账户没有可维护的旧版实盘做 T 执行。")
          }
          Button("刷新当前执行") { Task { try? await store.loadLegacySource() } }
            .disabled(store.operationInProgress)
        }
      }
      if let inventory = store.legacyInventory {
        Section("义务清单复核") {
          Text("清单保留历史记录，条目数量不代表未完成义务数量。停止新买入后仍保留旧执行绑定。")
            .font(.caption).foregroundStyle(.secondary)
          ForEach(LegacyMaintenanceReview.groups, id: \.key) { group in
            let rows = LegacyMaintenanceReview.rows(inventory, key: group.key)
            DisclosureGroup("\(group.title) · \(rows.count) 条") {
              ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                VStack(alignment: .leading, spacing: QuantXTheme.Spacing.small) {
                  Text("条目 \(index + 1)").font(.caption).foregroundStyle(.secondary)
                  Text(LegacyMaintenanceReview.describe(row)).font(.caption.monospaced())
                    .textSelection(.enabled)
                }
              }
            }
          }
          DisclosureGroup("复核标识") {
            Text("原执行：\(inventory.scope.runID)")
            Text("清单：\(inventory.operationID)")
            Text("摘要：\(inventory.hash)")
          }
          .font(.caption.monospaced()).textSelection(.enabled)
          if store.legacyConfirmationAttempted {
            Text("确认沿用原已复核清单。")
          } else {
            Toggle("我已核对清单与保留义务", isOn: $reviewed).disabled(store.operationInProgress)
          }
          if !store.legacyConfirmationAttempted {
            Button("重新准备清单") {
              do {
                try store.discardLegacyReview()
                reviewed = false
              } catch {}
            }.disabled(store.operationInProgress)
          }
        }
        if !store.legacyConfirmationAttempted && store.legacyTicket == nil {
          Section("维护窗口（北京时间）") {
            DatePicker("开始", selection: $windowStart, displayedComponents: [.date, .hourAndMinute])
            DatePicker("结束", selection: $windowEnd, displayedComponents: [.date, .hourAndMinute])
            TimelineView(.periodic(from: .now, by: 1)) { timeline in
              Button("获取维护确认") {
                Task {
                  try? await store.previewLegacyDrain(
                    windowStart: windowStart, windowEnd: windowEnd)
                }
              }
              .disabled(
                store.operationInProgress || !reviewed
                  || store.legacySource?.scope != inventory.scope
                  || store.legacySource?.draining != false || windowStart > timeline.date
                  || windowEnd <= timeline.date || windowStart >= windowEnd)
            }
            Text("进入维护窗口后获取短时确认，核对后再使用生物识别提交。")
              .font(.caption).foregroundStyle(.secondary)
          }
          .environment(\.timeZone, TimeZone(identifier: "Asia/Shanghai")!)
        }
      }
      if let ticket = store.legacyTicket {
        Section("核对并确认") {
          Text(
            "窗口：\(LegacyMaintenanceReview.time(ticket.windowStart)) 至 \(LegacyMaintenanceReview.time(ticket.windowEnd))"
          )
          Text("确认有效期至 \(LegacyMaintenanceReview.time(ticket.expiresAt))")
          TimelineView(.periodic(from: .now, by: 1)) { timeline in
            Button(store.legacyConfirmationAttempted ? "再次生物确认原请求" : "生物确认停止新买入") {
              Task { try? await store.confirmLegacyDrain() }
            }
            .buttonStyle(.borderedProminent)
            .disabled(
              store.operationInProgress
                || (!store.legacyConfirmationAttempted
                  && (!reviewed || ticket.expiresAt <= timeline.date
                    || ticket.windowStart > timeline.date || ticket.windowEnd <= timeline.date))
            )
          }
          if !store.legacyConfirmationAttempted {
            Button("重新选择窗口") { store.discardLegacyPreview() }
              .disabled(store.operationInProgress)
          }
        }
      }
      if store.legacyPreparationID != nil {
        Section("处理结果") {
          Text(LegacyMaintenanceReview.status(store))
          if store.legacyInventory == nil && !store.legacyConfirmationAttempted,
            let scope = store.legacyScope
          {
            Button("重试原清单请求") {
              Task {
                do {
                  try await store.prepareLegacyInventory(scope)
                  try await store.refreshLegacyStatus()
                } catch {}
              }
            }.disabled(store.operationInProgress)
            Button("放弃本次清单复核") {
              Task {
                do {
                  try store.discardLegacyReview()
                  try await store.loadLegacySource()
                } catch {}
              }
            }.disabled(store.operationInProgress)
          }
          if store.legacyChallengeID != nil {
            Button("恢复原确认结果") {
              Task {
                do {
                  try await store.recoverLegacyConfirmation()
                  if store.legacyCommandID != nil { try await store.refreshLegacyStatus() }
                } catch {}
              }
            }.disabled(store.operationInProgress)
          }
          if !store.legacyConfirmationAttempted || store.legacyCommandID != nil {
            Button("刷新处理状态") { Task { try? await store.refreshLegacyStatus() } }
              .disabled(store.operationInProgress)
          }
        }
      }
    }
    .navigationTitle("旧版做 T 维护")
    .task { try? await store.loadLegacySource() }
    .onChange(of: store.legacyInventory?.hash) { _, _ in reviewed = false }
  }
}

@MainActor
enum LegacyMaintenanceReview {
  static let groups: [(key: String, title: String)] = [
    ("unsubmitted_intent_ids_for_review", "将取消的未路由买入"), ("retained_client_order_ids", "保留的订单标识"),
    ("intents", "交易意图"), ("pending", "委托记录"), ("batches", "做 T 批次"),
    ("exit_plans", "退出计划"), ("correlations", "订单关联"), ("commands", "投递记录"),
    ("runtime_events", "回报收敛记录"),
  ]
  static func rows(_ inventory: TAssistantLegacyInventory, key: String) -> [GraphQLJSON] {
    guard let fields = try? TAssistantLegacyInventory.object(inventory.manifest),
      case .array(let rows) = fields[key]
    else { return [] }
    return rows
  }
  static func describe(_ value: GraphQLJSON) -> String {
    switch value {
    case .null: return "未记录"
    case .string(let text): return text
    case .boolean(let flag): return flag ? "是" : "否"
    case .integer(let number): return String(number)
    case .number(let number): return String(number)
    case .array(let rows): return rows.map(describe).joined(separator: "、")
    case .object(let fields):
      return fields.map { "\(labels[$0.key] ?? $0.key)：\(describe($0.value))" }.joined(
        separator: "\n")
    }
  }
  static func time(_ value: Date) -> String {
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "zh_CN")
    formatter.timeZone = TimeZone(identifier: "Asia/Shanghai")
    formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
    return formatter.string(from: value)
  }
  static func status(_ store: TTradeControlStore) -> String {
    if store.legacyConfirmationAttempted && store.legacyCommandID == nil {
      return "原确认投递结果未明确，请保留原操作并恢复查询。"
    }
    switch store.legacyStatus?.status {
    case .succeeded: return store.legacyCommandID == nil ? "清单已生成，等待复核。" : "已停止新买入，原订单和退出义务继续处理。"
    case .failed: return "命令未能完成，请核对原操作；不能据此认为旧义务已归零。"
    case .processing: return "服务端正在处理。"
    case .pending: return "请求已入队，等待服务端处理。"
    case .notFound: return store.legacyCommandID == nil ? "暂未查到清单请求，可使用原请求重试。" : "未查到原排空命令，结果仍待核对。"
    case nil: return "等待查询服务端处理结果。"
    }
  }
  private static let labels = [
    "id": "标识", "intent_id": "意图标识", "client_order_id": "订单标识", "broker_order_id": "券商委托标识",
    "instrument_code": "证券", "side": "方向", "direction": "方向", "volume": "数量", "limit_price": "委托价格",
    "status": "状态", "batch_id": "批次", "t_trade_role": "交易职责", "t_order_attempt": "委托轮次",
    "last_source_sequence": "回报序号", "t_order_original_created_at": "原始委托时间", "plan_id": "计划标识",
    "source_type": "来源类型", "source_id": "来源标识", "enabled": "启用", "protected_volume": "保护数量",
    "exited_volume": "已退出数量", "remaining_volume": "剩余数量", "auto_exit_authorized": "退出已授权",
    "config_version": "配置版本", "state_version": "状态版本", "target_volume": "目标数量",
    "target_amount": "目标金额",
    "executed_volume": "成交数量", "executed_price": "成交价格", "order_id": "委托标识",
    "entry_intent_id": "买入意图",
    "entry_filled_volume": "买入成交数量", "exit_filled_volume": "卖出成交数量", "message_id": "消息标识",
    "delivery_status": "投递状态", "expires_at": "有效期", "event_id": "事件标识", "event_type": "事件类型",
    "application_status": "收敛状态",
  ]
}
