import { useMemo } from 'react';

import { logger } from '@/core/errors/logger';

import type { LiquidatedStock, Position } from '../types';

interface UseLiquidationDataResult {
  liquidatedStocks: LiquidatedStock[];
  currentHoldings: Position[];
  isLoading: boolean;
  error: Error | null;
  refetch: () => void;
}

/**
 * 清仓数据查询 Hook
 * 负责获取当前持仓和已清仓股票数据
 */
export function useLiquidationData(): UseLiquidationDataResult {
  // TODO: 将来接入 GraphQL 查询
  const isLoading = false;
  const error = null;

  // Mock 已清仓股票数据
  const liquidatedStocks: LiquidatedStock[] = useMemo(
    () => [
      {
        id: 'liquidated-1',
        symbol: '000002',
        name: '万科A',
        quantity: 1000,
        sellPrice: 8.55,
        sellDate: '2024-03-15',
        realizedPnL: 550,
        realizedPnLPercent: 6.88,
        originalCost: 8.0,
      },
    ],
    []
  );

  // Mock 当前持仓数据
  const currentHoldings: Position[] = useMemo(
    () => [
      {
        id: 'pos-1',
        stockCode: '600519',
        instrumentName: '贵州茅台',
        volume: 100,
        canUseVolume: 100,
        avgPrice: 1680.5,
        lastPrice: 1720.0,
        marketValue: 172000,
        profitLoss: 3950,
        profitRate: 2.35,
      },
      {
        id: 'pos-2',
        stockCode: '000001',
        instrumentName: '平安银行',
        volume: 2000,
        canUseVolume: 2000,
        avgPrice: 12.8,
        lastPrice: 13.45,
        marketValue: 26900,
        profitLoss: 1300,
        profitRate: 5.08,
      },
    ],
    []
  );

  const refetch = () => {
    logger.info('模拟刷新数据');
  };

  return {
    liquidatedStocks,
    currentHoldings,
    isLoading,
    error,
    refetch,
  };
}
