export interface MonitorReasonPresentation {
  title: string;
  description: string;
}

const MONITOR_REASON_PRESENTATIONS: Record<string, MonitorReasonPresentation> =
  {
    TIMEOUT: {
      title: '健康检查响应超时',
      description: 'Monitor 在限定时间内没有收到目标服务响应。',
    },
    TLS_ERROR: {
      title: '安全连接握手失败',
      description: 'Monitor 无法与目标服务建立 TLS 安全连接。',
    },
    CONNECT_ERROR: {
      title: '无法连接目标服务',
      description: '目标地址拒绝连接、网络不可达，或服务进程尚未监听。',
    },
    PROTOCOL_ERROR: {
      title: '服务响应格式异常',
      description: '目标已响应，但返回内容不符合健康检查协议。',
    },
    HTTP_STATUS: {
      title: '健康检查返回异常状态',
      description: '目标服务可以连接，但健康端点返回了非成功 HTTP 状态。',
    },
    DEPENDENCY_NOT_READY: {
      title: '服务依赖尚未就绪',
      description: '目标进程仍在运行，但它依赖的组件未达到可用状态。',
    },
    SNAPSHOT_UNAVAILABLE: {
      title: '运行状态快照不可用',
      description: 'Monitor 未取得可用于判断该组件状态的最新快照。',
    },
    CONTROL_CONNECTION_OFFLINE: {
      title: 'QMT Agent 控制链路已断开',
      description:
        '本机 Agent 与 QuantX 服务端失去连接，当前无法接收控制命令。',
    },
    TRADING_RECONCILING: {
      title: 'QMT Agent 正在同步账户状态',
      description: '账户资产、持仓、委托和成交快照仍在同步，实盘交易暂时阻断。',
    },
    QMT_AGENT_NOT_RECONCILED: {
      title: 'QMT Agent 尚未完成账户对账',
      description:
        '账户资产、持仓、委托和成交快照完成对账前，实盘交易保持阻断。',
    },
    XTDATA_UNAVAILABLE: {
      title: 'MiniQMT 行情连接未就绪',
      description:
        'QMT Agent 无法使用 XTData，实时行情与全市场行情流当前不可用。',
    },
    XTTRADING_UNAVAILABLE: {
      title: 'MiniQMT 交易能力未就绪',
      description:
        '交易连接或券商账户尚未就绪，实盘交易当前不可用；行情能力独立判断。',
    },
    MARKET_STREAM_NOT_READY: {
      title: '全市场行情流尚未就绪',
      description:
        '行情连接已建立，但全市场数据流仍在同步或尚未达到新鲜度要求。',
    },
    MARKET_STREAM_OFFLINE: {
      title: 'QMT 行情连接离线',
      description:
        '网关没有当前有效的 QMT 行情连接；历史缓存不能证明行情在线。',
    },
    MARKET_STREAM_SYNCING: {
      title: '行情正在同步',
      description: '全市场快照、就绪确认或 Redis 提交尚未完成。',
    },
    MARKET_STREAM_STALE: {
      title: '行情供给已过期',
      description: '交易时段没有当前行情水位对应的新鲜度租约。',
    },
    MARKET_SNAPSHOT_INCOMPLETE: {
      title: '行情快照不完整',
      description: '网关尚未取得达到覆盖要求的完整行情快照。',
    },
    MARKET_REDIS_UNAVAILABLE: {
      title: '行情存储不可用',
      description: '网关无法读取 Redis 中的行情提交状态和新鲜度租约。',
    },
    MARKET_CALENDAR_UNAVAILABLE: {
      title: '交易日历暂不可用',
      description: '无法确认当前是否为交易时段，行情健康检查保持未就绪。',
    },
    ENGINE_MARKET_NOT_READY: {
      title: '引擎行情消费尚未就绪',
      description: 'Engine 的行情消费或同步水位未就绪；行情网关健康独立判断。',
    },
    QMT_HEALTH_CONNECT_ERROR: {
      title: '无法连接 QMT Agent 健康端点',
      description: 'Monitor 无法建立到本机 QMT Agent 健康服务的连接。',
    },
    QMT_HEALTH_TIMEOUT: {
      title: 'QMT Agent 健康检查超时',
      description: '本机 QMT Agent 没有在限定时间内响应健康检查。',
    },
    QMT_HEALTH_HTTP_STATUS: {
      title: 'QMT Agent 健康端点返回异常状态',
      description: '健康服务可以连接，但返回了协议未约定的 HTTP 状态。',
    },
    QMT_HEALTH_PROTOCOL_ERROR: {
      title: 'QMT Agent 健康响应无效',
      description: '健康端点已响应，但响应内容与约定的健康协议不一致。',
    },
    QMT_HEALTH_SCHEMA_MISMATCH: {
      title: 'QMT Agent 健康协议版本不匹配',
      description: '本机 Agent 与 Monitor 使用的健康快照结构版本不一致。',
    },
    QMT_AGENT_OFFLINE: {
      title: 'QMT Agent 当前离线',
      description: 'QuantX 没有检测到可用的本机 Agent 会话或有效心跳。',
    },
    QMT_AGENT_STALE: {
      title: 'QMT Agent 心跳已经过期',
      description: '本机 Agent 曾经在线，但最新心跳已超过允许的新鲜度。',
    },
    QMT_CONTROL_DEPENDENCY_UNAVAILABLE: {
      title: 'QMT Agent 控制依赖暂不可用',
      description:
        '控制连接仍然在线，但数据库或健康投影暂不可用；新增风险交易已安全暂停。',
    },
    QMT_CONTROL_TRANSPORT_LOST: {
      title: 'QMT Agent 控制传输已中断',
      description: 'Agent 控制 WebSocket 已真实断开，正在按退避策略重新连接。',
    },
    QMT_CONTROL_SESSION_REPLACED: {
      title: 'QMT Agent 控制会话已被替换',
      description: '同一登记设备建立了更新的控制连接，旧连接已被精确淘汰。',
    },
    QMT_DEVICE_REVOKED: {
      title: 'QMT Agent 设备授权已撤销',
      description: '本机 Agent 登记凭据已失效，需要重新登记后才能恢复连接。',
    },
    QMT_API_RESTARTED: {
      title: 'QuantX API 已重启',
      description: '旧 API 代际的 Agent 会话已结束，Agent 正在连接新的服务代际。',
    },
    QMT_ACCOUNT_MISMATCH: {
      title: 'QMT Agent 账户与授权账户不一致',
      description: '本机 MiniQMT 当前账户不在本次运行允许的唯一账户范围内。',
    },
    QMT_ENROLLMENT_REQUIRED: {
      title: 'QMT Agent 尚未完成本机登记',
      description: '当前 Windows 设备没有可用于启动实盘 Agent 的有效登记。',
    },
    QMT_RUNTIME_UNAVAILABLE: {
      title: '本机 MiniQMT 运行环境不可用',
      description: '启动预检没有找到可用的 MiniQMT 或 XTQuant 运行环境。',
    },
    QMT_LAUNCH_BLOCKED: {
      title: 'QMT Agent 启动已被阻断',
      description: '本机启动预检未通过，QMT Agent 未进入运行状态。',
    },
    EMERGENCY_STOP: {
      title: 'QMT Agent 处于紧急停止状态',
      description: '紧急停止门禁仍然生效，新的实盘交易命令不会下发。',
    },
    ACCOUNT_SAFETY_DISABLED: {
      title: '账户准入观测未启用',
      description: 'Monitor 当前没有启用账户交易准入快照的采集。',
    },
    ACCOUNT_SAFETY_UNOBSERVED: {
      title: '账户准入状态尚未观测',
      description: 'Monitor 尚未取得可用于判断账户交易准入状态的快照。',
    },
    ACCOUNT_SAFETY_CONNECT_ERROR: {
      title: '无法连接账户准入观测端点',
      description: 'Monitor 无法建立到 API 账户准入观测端点的连接。',
    },
    ACCOUNT_SAFETY_TIMEOUT: {
      title: '账户准入观测响应超时',
      description: 'API 没有在限定时间内返回账户准入观测快照。',
    },
    ACCOUNT_SAFETY_HTTP_STATUS: {
      title: '账户准入观测返回异常状态',
      description: '观测端点可以连接，但返回了非成功 HTTP 状态。',
    },
    ACCOUNT_SAFETY_PROTOCOL_ERROR: {
      title: '账户准入观测响应无效',
      description: '观测端点已响应，但响应内容不符合约定协议。',
    },
    ACCOUNT_SAFETY_SCHEMA_MISMATCH: {
      title: '账户准入观测协议版本不匹配',
      description: 'API 与 Monitor 使用的账户准入快照结构版本不一致。',
    },
  };

export function monitorReasonPresentation(
  reasonCode: string | null | undefined,
  targetName: string
): MonitorReasonPresentation {
  const normalized = reasonCode?.trim().toUpperCase();
  if (normalized && MONITOR_REASON_PRESENTATIONS[normalized]) {
    return MONITOR_REASON_PRESENTATIONS[normalized];
  }
  return {
    title: `${targetName} 报告异常`,
    description:
      'Monitor 已检测到该服务未就绪，但当前版本尚未收录这一原因的可读说明。',
  };
}
