import { useMemo } from 'react';

/**
 * 交易金额计算逻辑
 */
export function useTradingCalculation(quantity: string, price: string) {
  return useMemo(() => {
    const estimatedAmount =
      quantity && price ? parseInt(quantity) * parseFloat(price) : 0;
    const estimatedFees = estimatedAmount * 0.0005; // 0.05% 手续费
    const estimatedTotal = estimatedAmount + estimatedFees;

    return {
      estimatedAmount,
      estimatedFees,
      estimatedTotal,
    };
  }, [quantity, price]);
}
