import { useIntradayTrendData } from '@/hooks';

import type { Position } from '../types';

export function useHoldingIntradayTrend(holding: Position) {
  const { lineData, visibleRange, loading, anchorDate } = useIntradayTrendData(
    holding.stockCode,
    '1d'
  );

  return {
    data: lineData,
    visibleRange,
    loading,
    anchorDate,
  };
}
