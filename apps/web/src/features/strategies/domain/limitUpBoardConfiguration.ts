import { StrategyRunMode } from '@/generated/gql/graphql';

import { type StrategyConfigValue } from '../hooks/types';

function timeValue(value: StrategyConfigValue | undefined, fallback: unknown) {
  return String(value ?? fallback ?? '');
}

function parseTradingTime(value: string) {
  const match = value.match(/^(\d{2}):(\d{2})(?::(\d{2}))?$/);
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  const seconds = Number(match[3] || 0);
  if (hours > 23 || minutes > 59 || seconds > 59) return null;
  return hours * 3600 + minutes * 60 + seconds;
}

export function validateLimitUpBoardConfiguration(
  config: Record<string, StrategyConfigValue>,
  runMode: StrategyRunMode
) {
  const errors: string[] = [];
  const start = timeValue(config.entry_start_time, '09:30');
  const end = timeValue(config.entry_end_time, '14:50');
  const startSeconds = parseTradingTime(start);
  const endSeconds = parseTradingTime(end);
  if (startSeconds === null) {
    errors.push('最早入场时间格式无效');
  }
  if (endSeconds === null) {
    errors.push('最晚入场时间格式无效');
  }
  if (
    startSeconds !== null &&
    endSeconds !== null &&
    startSeconds > endSeconds
  ) {
    errors.push('最早入场时间不能晚于最晚入场时间');
  }
  const position = Number(config.target_position_pct ?? 0);
  if (!Number.isFinite(position) || position <= 0 || position > 0.3) {
    errors.push('目标仓位必须在 0% 到 30% 之间');
  }
  if (
    runMode === StrategyRunMode.Backtest &&
    Number(config.initial_capital ?? 0) < 10000
  ) {
    errors.push('回测初始资金不能低于 10,000 元');
  }
  if (
    runMode === StrategyRunMode.Live &&
    !String(config.account_id ?? '').trim()
  ) {
    errors.push('实盘模式必须绑定当前交易账户');
  }
  return errors;
}
