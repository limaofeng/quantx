const exitRuleLabels: Record<string, string> = {
  ADAPTIVE_VOLUME_PRICE_TRAILING: '量价动态止盈',
  GROSS_TAKE_PROFIT: '收益止盈',
  HARD_STOP: '硬止损',
  LIMIT_UP_BREAK: '涨停开板',
  LIMIT_UP_TOUCH: '触及涨停',
  MANUAL_TRIGGER: '人工计划触发',
  MAX_HOLDING_DAYS: '最大持有日',
  NET_TAKE_PROFIT: '净收益止盈',
  RAPID_PROFIT_REVERSAL: '快速收益反转',
  STOP_PRICE: '止损价',
  TARGET_PRICE: '目标价',
  TIME_OF_DAY: '指定时间',
  TRAILING_NET_PROFIT: '动态保盈',
  TRAILING_PRICE_DRAWDOWN: '价格回撤',
};

export function getExitRuleLabel(ruleType?: string | null) {
  const normalizedType = String(ruleType || '')
    .trim()
    .toUpperCase();
  if (!normalizedType) return '退出规则';
  return exitRuleLabels[normalizedType] || '自定义退出规则';
}
