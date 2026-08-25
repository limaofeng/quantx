"""关系型数据库表的中文说明。"""

from __future__ import annotations

from sqlalchemy import MetaData

TABLE_COMMENTS: dict[str, str] = {
  "account_trading_rollouts": "证券账户实盘灰度与熔断状态",
  "account_trading_rollout_events": "证券账户实盘灰度操作审计事件",
  "accounts": "证券账户资产信息",
  "agent_devices": "QMT Agent 设备注册信息",
  "agent_enrollment_codes": "QMT Agent 设备登记码",
  "agent_report_inbox": "QMT Agent 回报持久化收件箱",
  "announcement_sync_runs": "上市公司公告同步运行记录",
  "auth_audit_events": "认证与安全审计事件",
  "auth_consumed_refresh_tokens": "已消费刷新令牌摘要",
  "auth_device_sessions": "用户设备登录会话",
  "auth_user_account_access": "用户与证券账户授权关系",
  "auth_users": "QuantX 本地认证用户",
  "ai_assistant_threads": "产品内 AI Assistant 对话线程",
  "ai_assistant_messages": "AI Assistant 用户可见消息",
  "ai_assistant_session_items": "AI Assistant 模型会话历史项",
  "ai_assistant_runs": "AI Assistant 可恢复运行任务",
  "ai_assistant_events": "AI Assistant 持久化流式事件",
  "ai_assistant_tool_calls": "AI Assistant 工具调用与审批审计",
  "ai_assistant_deletion_audits": "AI Assistant 对话删除无内容审计",
  "ai_runtime_settings": "AI Runtime 全局非敏感动态配置",
  "ai_runtime_settings_audits": "AI Runtime 配置变更审计",
  "broker_position_snapshots": "券商持仓全量快照",
  "closed_position_cycles": "已平仓持仓周期记录",
  "conditional_liquidation_orders": "条件清仓任务",
  "auto_exit_plans": "Engine 统一自动退出计划",
  "auto_exit_plan_events": "自动退出计划幂等事件与审计",
  "daily_asset_position_snapshots": "每日资产快照持仓明细",
  "daily_asset_snapshots": "每日账户与策略资产快照",
  "daily_signal_definitions": "日级选股信号定义",
  "daily_signal_runs": "日级信号批量计算运行记录",
  "divid_factors": "证券除权除息与复权因子",
  "engine_command_outbox": "策略引擎控制命令持久化发件箱",
  "entry_automation_gates": "账户级全局自动买入暂停门",
  "entry_plan_authorization_consumptions": "自动买入授权真实成交消费流水",
  "entry_plan_authorization_events": "自动买入授权安全审计事件",
  "entry_plan_authorization_grants": "建仓计划精确自动买入授权",
  "exit_plan_replay_projections": "卖出计划历史回放生命周期投影",
  "financial_balance_sheet": "上市公司资产负债表",
  "financial_capital": "上市公司股本结构",
  "financial_cash_flow": "上市公司现金流量表",
  "financial_holder_num": "上市公司股东户数",
  "financial_income_statement": "上市公司利润表",
  "financial_metric_snapshots": "上市公司财务指标快照",
  "financial_metric_roe_qualities": "上市公司ROE指标独立质量状态",
  "financial_sync_code_audits": "上市公司逐标的财务同步验证记录",
  "financial_shareholder": "上市公司主要股东信息",
  "financial_sync_runs": "上市公司财务数据同步运行记录",
  "first_board_candidate_preferences": "账户首板候选偏好",
  "first_board_model_releases": "首板晋级模型发布证据门禁",
  "first_board_promotion_assessments": "首板晋级确定性评估快照",
  "holidays": "证券市场节假日与交易日历",
  "indicator_snapshots": "证券日级技术指标快照",
  "instruments": "证券标的基础信息",
  "ios_business_notification_receipts": "iOS 业务通知全局幂等投影回执",
  "ios_notification_events": "iOS 随机通知事件与解锁后路由元数据",
  "ios_notification_outbox": "iOS 最小隐私推送持久化发件箱",
  "ios_push_category_preferences": "iOS 安装实例通知类别偏好",
  "ios_push_registrations": "iOS 会话绑定的 APNs 设备注册",
  "liquidation_logs": "清仓任务执行日志",
  "liquidation_orders": "清仓任务",
  "limit_up_board_assistant_configs": "账户级打板助手配置",
  "limit_up_board_assistant_projections": "账户级打板助手读投影",
  "limit_up_board_candidate_arms": "账户当日人工布防的打板候选",
  "limit_up_board_replay_jobs": "账户级打板助手历史回放任务",
  "limit_up_board_replay_scenarios": "打板助手回放固定成交情景",
  "limit_up_board_universe_snapshots": "打板助手不可变历史候选池快照",
  "limit_up_chain_snapshots": "涨停连板梯队不可变快照",
  "limit_up_lifecycle_snapshots": "涨停候选生命周期不可变快照",
  "limit_up_radar_events": "全市场打板雷达阶段事件",
  "limit_up_research_artifacts": "首板候选市场级共享AI研究产物",
  "limit_up_research_jobs": "首板候选AI研究任务",
  "market_data_request": "行情数据传输请求",
  "market_data_transfer": "行情数据分片传输记录",
  "trade_confirmation_challenges": "敏感交易操作一次性确认挑战",
  "operational_alerts": "运行异常告警及处置状态",
  "orders": "券商委托订单",
  "pending_trade_orders": "券商委托编号生成前的待处理订单",
  "positions": "证券账户持仓",
  "redemption_records": "资金赎回记录",
  "runtime_component_heartbeats": "运行组件心跳状态",
  "sector_stocks": "板块与成分股关联关系",
  "sectors": "证券板块基础信息",
  "stock_announcements": "上市公司公告索引",
  "stock_repurchase_events": "上市公司股票回购事件",
  "strategies": "策略模板",
  "strategy_backtests": "策略回测结果元数据",
  "strategy_decision_traces": "策略决策审计轨迹",
  "strategy_grid_book_snapshots": "策略网格账本查询快照",
  "strategy_order_correlations": "策略订单与券商回报关联关系",
  "strategy_performance_samples": "策略运行绩效采样",
  "strategy_run_positions": "策略运行时持仓状态",
  "strategy_run_states": "策略运行时资金与算法状态",
  "strategy_runs": "策略运行实例",
  "strategy_runtime_events": "策略引擎待处理运行事件",
  "strategy_trade_intents": "策略交易意图及执行状态",
  "t_trade_batches": "正向做 T 批次生命周期",
  "t_trade_candidate_outcomes": "做 T 候选因果结果成熟状态",
  "t_trade_global_configs": "账户级全局做 T 配置",
  "t_trade_global_monitor_projections": "账户级全局做 T 监控投影",
  "t_trade_imported_entries": "全局做 T 导入成交记录",
  "t_trade_instrument_profiles": "做 T 标的时点画像事实",
  "t_trade_opportunity_evaluations": "做 T 机会评估不可变审计证据",
  "t_trade_replay_projections": "做 T 历史回放生命周期投影",
  "trade_command_outbox": "QMT Agent 交易命令持久化发件箱",
  "trades": "券商成交记录",
  "watchlist_items": "账户自选证券",
  "watchlist_groups": "账户自选分组",
  "watchlist_group_memberships": "账户自选分组成员关系",
}

LATE_LOADED_TABLES = frozenset({"divid_factors"})


def apply_table_comments(metadata: MetaData) -> None:
  """向完整模型元数据写入表注释，并拒绝无注释或失效的映射。"""

  table_names = set(metadata.tables)
  comment_names = set(TABLE_COMMENTS)
  missing = sorted(table_names - comment_names)
  unknown = sorted(comment_names - table_names - LATE_LOADED_TABLES)
  if missing or unknown:
    raise RuntimeError(
      "数据库表注释映射与 SQLAlchemy 元数据不一致："
      f"missing={missing}, unknown={unknown}"
    )

  for table_name in table_names:
    comment = TABLE_COMMENTS[table_name]
    metadata.tables[table_name].comment = comment
