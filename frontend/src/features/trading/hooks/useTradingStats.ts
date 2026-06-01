import { useMemo } from 'react';

import type { EnrichedTransaction } from '@/shared/types';

interface UseTradingStatsResult {
  totalTransactions: number;
  profitableTransactions: number;
  winRate: number;
  totalProfit: number;
}

/**
 * 交易统计 Hook
 * 根据交易记录计算统计指标
 */
export function useTradingStats(
  transactions: EnrichedTransaction[]
): UseTradingStatsResult {
  return useMemo(() => {
    const totalTransactions = transactions.length;

    // 计算盈利交易数量（模拟逻辑）
    const profitableTransactions = transactions.filter(t => {
      // 使用 ID 的字符编码模拟盈利判断
      return t.id.charCodeAt(0) % 2 === 0;
    }).length;

    // 计算胜率
    const winRate =
      totalTransactions > 0
        ? (profitableTransactions / totalTransactions) * 100
        : 0;

    // 计算总收益（模拟逻辑）
    const totalProfit = transactions.reduce((sum, t) => {
      const profit =
        t.totalAmount * (t.id.charCodeAt(0) % 2 === 0 ? 0.03 : -0.02);
      return sum + profit;
    }, 0);

    return {
      totalTransactions,
      profitableTransactions,
      winRate,
      totalProfit,
    };
  }, [transactions]);
}
