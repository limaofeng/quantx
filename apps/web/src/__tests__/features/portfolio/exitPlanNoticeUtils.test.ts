import { describe, expect, it } from 'vitest';

import { buildExitPlanNotices } from '@/features/portfolio/components/exitPlanNoticeUtils';

describe('buildExitPlanNotices', () => {
  it('renders a closed market as one informational waiting state', () => {
    expect(
      buildExitPlanNotices({
        dataQuality: 'MARKET_CLOSED',
        lastError: 'market_data_stale',
      })
    ).toEqual([
      {
        key: 'market-closed',
        message: '已收盘，等待下一交易时段',
        tone: 'info',
      },
    ]);
  });

  it('deduplicates the stale quality and stale error codes', () => {
    expect(
      buildExitPlanNotices({
        dataQuality: 'MARKET_DATA_STALE',
        lastError: 'market_data_stale',
      })
    ).toEqual([
      {
        key: 'market-data-stale',
        message: '实时行情超过 10 秒未更新，自动卖出已暂停',
        tone: 'warning',
      },
    ]);
  });

  it('prefers the actionable stream reason over the generic quality state', () => {
    expect(
      buildExitPlanNotices({
        dataQuality: 'MARKET_DATA_STALE',
        lastError: 'MARKET_DATA_STREAM_NOT_READY',
      })
    ).toEqual([
      {
        key: 'market-stream-not-ready',
        message: '实时行情链路未就绪，自动卖出已暂停',
        tone: 'warning',
      },
    ]);
  });

  it('does not repeat an approval notice stored as an error code', () => {
    expect(
      buildExitPlanNotices({
        dataQuality: 'GOOD',
        lastError: 'exit_intent_awaiting_approval',
        pendingIntentId: 'intent-1',
      })
    ).toEqual([
      {
        key: 'pending-intent',
        message: '卖出意图等待人工确认',
        tone: 'info',
      },
    ]);
  });

  it('shows the actionable rebuild instruction instead of an internal sticky code', () => {
    expect(
      buildExitPlanNotices({
        dataQuality: 'GOOD',
        lastError: 'QUARANTINE_REPAIRED:intent-old',
        recoveryMessage:
          '隔离委托已完成券商事实修复。旧计划不能恢复；请取消旧计划，再按最新持仓重新创建并授权。',
      })
    ).toEqual([
      {
        key: 'plan-recovery',
        message:
          '隔离委托已完成券商事实修复。旧计划不能恢复；请取消旧计划，再按最新持仓重新创建并授权。',
        tone: 'warning',
      },
    ]);
  });
});
