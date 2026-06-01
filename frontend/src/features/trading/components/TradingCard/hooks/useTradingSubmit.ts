import type React from 'react';
import { useCallback, useMemo } from 'react';

import { useToast } from '@/hooks/use-toast';
import type { Stock } from '@/shared/types';

import { useCurrentAccount } from '../../../../dashboard/hooks';
import { useCreateOrder } from '../../../hooks';

/**
 * 交易提交逻辑
 */
export function useTradingSubmit(
  _userId: string = 'demo-user',
  onSuccess?: () => void
) {
  const { toast } = useToast();
  const { loading: isSubmitting, createOrder } = useCreateOrder();
  const { data: _accountData } = useCurrentAccount(); // 获取账号数据用于刷新等逻辑

  const handleSubmit = useCallback(
    async (
      e: React.SyntheticEvent,
      tradeType: 'buy' | 'sell',
      orderType: string,
      selectedStock: Stock | null,
      quantity: string,
      price: string,
      resetForm: () => void
    ) => {
      e.preventDefault();

      if (!selectedStock || !quantity || !price) {
        toast({
          title: '信息不完整',
          description: '请填写完整的交易信息',
          variant: 'destructive',
        });
        return;
      }

      const quantityNum = parseInt(quantity);
      const priceNum = parseFloat(price);

      try {
        const result = await createOrder({
          stockCode: selectedStock.code!,
          type: tradeType.toUpperCase(), // BUY or SELL
          priceType: orderType.toUpperCase(), // LIMIT/MARKET/VWAP
          price: priceNum,
          volume: quantityNum,
        });

        if (result.error) {
          toast({
            title: '交易失败',
            description: result.error.message || '请检查输入信息后重试',
            variant: 'destructive',
          });
        } else {
          toast({
            title: '交易成功',
            description: '订单已提交',
          });
          resetForm();
          onSuccess?.();
        }
      } catch (error) {
        toast({
          title: '系统错误',
          description: error instanceof Error ? error.message : '交易提交异常',
          variant: 'destructive',
        });
      }
    },
    [createOrder, toast, onSuccess]
  );

  return useMemo(
    () => ({
      handleSubmit,
      isSubmitting,
    }),
    [handleSubmit, isSubmitting]
  );
}
