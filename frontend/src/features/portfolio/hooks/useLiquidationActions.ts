import { useState, useCallback } from 'react';

import { logger } from '@/core/errors/logger';

interface UseLiquidationActionsResult {
  isLoading: boolean;
  error: Error | null;
  liquidateMultiple: (holdingIds: string[]) => Promise<void>;
  redeemCash: (amount: number) => Promise<void>;
}

/**
 * 清仓操作 Hook
 * 负责执行清仓和现金赎回操作
 */
export function useLiquidationActions(): UseLiquidationActionsResult {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  // 部分清仓操作
  const liquidateMultiple = useCallback(async (holdingIds: string[]) => {
    try {
      setIsLoading(true);
      setError(null);

      // 模拟网络延迟
      await new Promise(resolve => setTimeout(resolve, 2000));

      logger.info(`模拟部分清仓: ${holdingIds.join(', ')}`);
    } catch (error) {
      logger.error('部分清仓失败:', error);
      setError(error instanceof Error ? error : new Error('部分清仓失败'));
      throw error;
    } finally {
      setIsLoading(false);
    }
  }, []);

  // 赎回现金操作
  const redeemCash = useCallback(async (amount: number) => {
    try {
      setIsLoading(true);
      setError(null);

      // 模拟网络延迟
      await new Promise(resolve => setTimeout(resolve, 1000));

      logger.info(`模拟赎回现金: ${amount}`);
    } catch (error) {
      logger.error('赎回现金失败:', error);
      setError(error instanceof Error ? error : new Error('赎回现金失败'));
      throw error;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return {
    isLoading,
    error,
    liquidateMultiple,
    redeemCash,
  };
}
