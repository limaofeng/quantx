import { useState, useMemo } from 'react';

import {
  addShanghaiDays,
  getShanghaiDateKey,
} from '@/components/trading-chart/utils/time-utils';
import type { EnrichedTransaction } from '@/shared/types';

import { useTodayTrades, useHistoryTrades } from './useTrading';

interface TradeRecordInput {
  tradedId: string;
  stockCode: string;
  stockName: string;
  orderType: number;
  tradedTime: number;
  tradedPrice: number;
  tradedVolume: number;
  tradedAmount: number;
}

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

  totalAmount: number;
}

/**
 * 将 GraphQL Trade 类型转换为 EnrichedTransaction 类型
 */
function tradeToTransaction(trade: TradeRecordInput): EnrichedTransaction {
  const stockName = trade.stockName || trade.stockCode || '';
  const tradedTimestamp = trade.tradedTime;
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

  const type = Number(trade.orderType) === 23 ? 'buy' : 'sell';

  return {
    id: trade.tradedId,
    type,
    stockCode: trade.stockCode,
    stockName,
    quantity: trade.tradedVolume,
    price: trade.tradedPrice,
    totalAmount: trade.tradedAmount,
    status: 'filled',
    orderTime: tradedTimeValue,
    fillTime: tradedTimeValue,
    createdAt: tradedTimeValue,
    stock: {
      id: trade.stockCode,
      stockCode: trade.stockCode,
      code: trade.stockCode,
      name: stockName,
    },
  };
}

export function useTradeRecords(
  accountId: string | undefined,
  itemsPerPage: number = 10,
  initialTimeFilter: string = '30days'
): UseTradeRecordsResult {
  // 筛选状态
  const [typeFilter, setTypeFilter] = useState<string>('all');
  const [timeFilter, setTimeFilter] = useState<string>(initialTimeFilter);
  const [currentPage, setCurrentPage] = useState(1);

  // Calculate date range for history query
  const dateRange = useMemo(() => {
    const end = new Date();
    let start = end;

    switch (timeFilter) {
      case 'today':
        return { startDate: '', endDate: getShanghaiDateKey(end) };
      case '7days':
        start = addShanghaiDays(end, -7);
        break;
      case '30days':
        start = addShanghaiDays(end, -30);
        break;
      default:
        start = addShanghaiDays(end, -30); // Default to 30 days
    }

    return {
      startDate: getShanghaiDateKey(start),
      endDate: getShanghaiDateKey(end),
    };
  }, [timeFilter]);

  // URQL 查询
  const { trades: todayTrades, loading: todayLoading } =
    useTodayTrades(accountId);
  const { trades: historyTrades, loading: historyLoading } = useHistoryTrades(
    accountId || '',
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
      totalAmount: filteredTransactions.reduce(
        (sum, transaction) => sum + transaction.totalAmount,
        0
      ),
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
    ]
  );
}
