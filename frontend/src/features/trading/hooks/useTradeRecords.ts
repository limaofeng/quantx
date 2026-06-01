import { format, subDays } from 'date-fns';
import { useState, useMemo } from 'react';

import type { EnrichedTransaction } from '@/shared/types';

import { useTodayTrades, useHistoryTrades } from './useTrading';
import { useTradingStats } from './useTradingStats';

interface UseTradeRecordsResult {
  // 原始数据
  recentTransactions: EnrichedTransaction[];
  isLoading: boolean;

  // 筛选状态
  typeFilter: string;
  timeFilter: string;
  currentPage: number;

  // 状态更新函数
  setTypeFilter: (filter: string) => void;
  setTimeFilter: (filter: string) => void;
  setCurrentPage: (page: number) => void;

  // 计算后的数据
  filteredTransactions: EnrichedTransaction[];
  paginatedTransactions: EnrichedTransaction[];
  totalPages: number;
  startIndex: number;

  // 统计属性
  winRate: number;
  totalProfit: number;
}

/**
 * 将 GraphQL Trade 类型转换为 EnrichedTransaction 类型
 */
function tradeToTransaction(trade: any): EnrichedTransaction {
  const stockName = trade.stockName || trade.stockCode || '';
  const tradedTimestamp = trade.tradedTime ?? trade.tradeTime;
  const normalizedTimestamp = Number(tradedTimestamp);
  const tradedTimeValue =
    tradedTimestamp == null || Number.isNaN(normalizedTimestamp)
      ? ''
      : (() => {
          const ms =
            String(tradedTimestamp).length > 10
              ? normalizedTimestamp
              : normalizedTimestamp * 1000;
          const parsed = new Date(ms);
          return Number.isNaN(parsed.getTime()) ? '' : parsed.toISOString();
        })();

  const directionText = String(trade.direction || '').toUpperCase();
  const orderTypeValue = Number.isFinite(Number(trade.orderType))
    ? Number(trade.orderType)
    : Number.NEGATIVE_INFINITY;
  const directionValue = Number.isFinite(Number(trade.direction))
    ? Number(trade.direction)
    : Number.NEGATIVE_INFINITY;

  const type =
    directionText === 'BUY' ||
    directionText === 'BUY_OPEN' ||
    directionText === 'BUY_TO_COVER' ||
    directionValue > 0
      ? 'buy'
      : directionText === 'SELL' ||
          directionText === 'SELL_SHORT' ||
          directionText === 'SELL_TO_CLOSE' ||
          directionValue < 0 ||
          orderTypeValue < 0
        ? 'sell'
        : orderTypeValue > 0
          ? 'buy'
          : 'sell';

  return {
    id: trade.tradedId ?? trade.id,
    type,
    stockCode: trade.stockCode,
    stockName,
    quantity: trade.tradedVolume ?? trade.quantity ?? 0,
    price: trade.tradedPrice ?? trade.price ?? 0,
    totalAmount: trade.tradedAmount ?? trade.amount ?? 0,
    status: 'filled',
    orderTime: tradedTimeValue,
    fillTime: tradedTimeValue,
    commission: trade.fee || trade.commission || 0,
    stock: {
      id: trade.stockCode,
      stockCode: trade.stockCode,
      code: trade.stockCode,
      name: stockName,
    } as any,
  };
}

export function useTradeRecords(
  userId: string = 'demo-user',
  itemsPerPage: number = 10,
  initialTimeFilter: string = '30days'
): UseTradeRecordsResult {
  // 筛选状态
  const [typeFilter, setTypeFilter] = useState<string>('all');
  const [timeFilter, setTimeFilter] = useState<string>(initialTimeFilter);
  const [currentPage, setCurrentPage] = useState(1);

  // 暂时使用 '300000013250' 作为默认 accountId，如果传入 demo-user
  const accountId = userId === 'demo-user' ? '300000013250' : userId;

  // Calculate date range for history query
  const dateRange = useMemo(() => {
    const end = new Date();
    let start = new Date();

    switch (timeFilter) {
      case 'today':
        return { startDate: '', endDate: format(end, 'yyyy-MM-dd') };
      case '7days':
        start = subDays(end, 7);
        break;
      case '30days':
        start = subDays(end, 30);
        break;
      default:
        start = subDays(end, 30); // Default to 30 days
    }

    return {
      startDate: format(start, 'yyyy-MM-dd'),
      endDate: format(end, 'yyyy-MM-dd'),
    };
  }, [timeFilter]);

  // URQL 查询
  const { trades: todayTrades, loading: todayLoading } =
    useTodayTrades(accountId);
  const { trades: historyTrades, loading: historyLoading } = useHistoryTrades(
    accountId,
    dateRange.startDate,
    dateRange.endDate
  );

  // 转换为 EnrichedTransaction 类型
  const recentTransactions = useMemo(() => {
    let sourceData = [];
    if (timeFilter === 'today') {
      sourceData = todayTrades || [];
    } else {
      sourceData = historyTrades || [];
    }
    return sourceData.map(tradeToTransaction);
  }, [todayTrades, historyTrades, timeFilter]);

  const isLoading = timeFilter === 'today' ? todayLoading : historyLoading;

  // 统计逻辑
  const stats = useTradingStats(recentTransactions);

  // 应用筛选逻辑
  const filteredTransactions = useMemo(() => {
    return recentTransactions.filter((transaction: EnrichedTransaction) => {
      // 交易类型筛选
      if (typeFilter !== 'all' && transaction.type !== typeFilter) {
        return false;
      }
      return true;
    });
  }, [recentTransactions, typeFilter]);

  // 分页逻辑
  const totalPages = Math.ceil(filteredTransactions.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const paginatedTransactions = useMemo(() => {
    return filteredTransactions.slice(startIndex, startIndex + itemsPerPage);
  }, [filteredTransactions, startIndex, itemsPerPage]);

  return useMemo(
    () => ({
      recentTransactions,
      isLoading,
      typeFilter,
      timeFilter,
      currentPage,
      setTypeFilter,
      setTimeFilter,
      setCurrentPage,
      filteredTransactions,
      paginatedTransactions,
      totalPages,
      startIndex,
      winRate: stats.winRate,
      totalProfit: stats.totalProfit,
    }),
    [
      recentTransactions,
      isLoading,
      typeFilter,
      timeFilter,
      currentPage,
      filteredTransactions,
      paginatedTransactions,
      totalPages,
      startIndex,
      stats.winRate,
      stats.totalProfit,
    ]
  );
}
