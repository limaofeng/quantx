import { formatNumber } from './utils';

const replayReasonLabels: Readonly<Record<string, string>> = {
  MONITOR_ENGINE_EXIT_PLAN: '持续监控已有批次的退出计划',
  WAITING_FOR_EXIT_PLAN_REGISTRATION: '等待退出计划登记，暂不生成新交易意图',
  MINIMUM_COVERAGE_NOT_REACHED: '行情窗口覆盖不足，继续积累样本',
  T_TRADE_OPPORTUNITY_CANDIDATE_LATCHED: '机会达到候选条件，已锁存候选',
  T_TRADE_GLOBAL_CONFIG_UPDATED: '全局参数已更新，同步退出策略',
  profit_armed: '止盈保护已激活',
  monitoring: '继续监控退出条件',
  opportunity_observed: '已评估交易机会',
  no_trade: '本次不生成交易意图',
  manual_confirmation_required: '候选需要确认；回放按隔离测试规则处理',
  t_trade_opportunity_candidate: '已形成做 T 机会候选',
  exit_plan_missing: '退出计划尚未登记',
};

export function replayReasonLabel(reason: string) {
  return replayReasonLabels[reason] || reason;
}

export function replayEvidenceUnavailableMessage(reason?: string | null) {
  const messages: Record<string, string> = {
    SIGNAL_ARCHIVE_NOT_RECORDED:
      '这个历史版本未保存真实信号归档。请新建或重跑回放；决策审计仍可单独查看，不能据此推导信号。',
    AUDIT_ARCHIVE_NOT_RECORDED:
      '这个历史版本未保存可读取的决策审计归档，请重跑回放。',
    ARCHIVE_UNAVAILABLE:
      '该回测版本的证据归档尚未生成或文件缺失。请检查回放状态，完成后刷新，必要时重跑。',
    ARCHIVE_NOT_SEALED: '该版本的归档尚未密封，暂不能作为历史证据展示。',
    ARCHIVE_IDENTITY_MISMATCH:
      '归档身份与当前回测版本不一致，已阻止展示。请检查该版本归档。',
    ARCHIVE_INTEGRITY_FAILED:
      '归档完整性校验失败，已阻止展示。请检查文件或重跑回放。',
  };
  return (
    messages[reason || ''] || '当前版本的回放证据不可用，请刷新或检查回放状态。'
  );
}

export function replayIntentTarget(intent?: {
  targetVolume?: number | null;
  targetAmount?: number | null;
  targetPositionPct?: number | null;
}) {
  if (intent?.targetVolume != null)
    return `${formatNumber(intent.targetVolume, 0)} 股`;
  if (intent?.targetAmount != null)
    return `¥${formatNumber(intent.targetAmount, 2)}`;
  if (intent?.targetPositionPct != null)
    return `仓位 ${formatNumber(intent.targetPositionPct * 100, 2)}%`;
  return '由定量层确定';
}
