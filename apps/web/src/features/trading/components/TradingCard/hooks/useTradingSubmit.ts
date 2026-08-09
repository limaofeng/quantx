import type React from 'react';
import { useCallback, useMemo } from 'react';

import { useTradingSafety } from '@/features/trading-safety';
import { useToast } from '@/hooks/use-toast';
import type { Stock } from '@/shared/types';

import { useCurrentAccount } from '../../../../dashboard/hooks';
import { useCreateOrder } from '../../../hooks';

function getSelectedStockCode(stock: Stock) {
  return stock.code || stock.stockCode || stock.id || '';
}

function getOrderRemark(tradeType: 'buy' | 'sell', stockCode: string) {
  return `交易控制台${tradeType === 'buy' ? '买入' : '平仓'}: ${stockCode}`;
}

/**
 * 交易提交逻辑
 */
export function useTradingSubmit(
  onSuccessOrLegacyUserId?: (() => void) | string,
  legacyOnSuccess?: () => void
) {
  const onSuccess =
    typeof onSuccessOrLegacyUserId === 'function'
      ? onSuccessOrLegacyUserId
      : legacyOnSuccess;
  const { toast } = useToast();
  const { loading: isSubmitting, createOrder } = useCreateOrder();
  const { data: accountData } = useCurrentAccount();
  const { canTrade, blockedReasons } = useTradingSafety();
  const accountId = accountData?.currentAccount?.id;

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

      if (!canTrade) {
        toast({
          title: '交易安全门禁已阻断',
          description: blockedReasons[0] || '请先恢复账户安全状态',
          variant: 'destructive',
        });
        return;
      }

      if (!accountId) {
        toast({
          title: '账户不可用',
          description: '未连接资金账户，无法提交委托',
          variant: 'destructive',
        });
        return;
      }

      if (!selectedStock || !quantity || !price) {
        toast({
          title: '信息不完整',
          description: '请填写完整的交易信息',
          variant: 'destructive',
        });
        return;
      }

      const stockCode = getSelectedStockCode(selectedStock);
      const quantityNum = parseInt(quantity, 10);
      const priceNum = parseFloat(price);

      if (
        !stockCode ||
        !Number.isFinite(quantityNum) ||
        quantityNum <= 0 ||
        !Number.isFinite(priceNum) ||
        priceNum <= 0
      ) {
        toast({
          title: '交易参数无效',
          description: '请检查证券代码、价格和委托数量',
          variant: 'destructive',
        });
        return;
      }

      try {
        const result = await createOrder({
          stockCode,
          type: tradeType.toUpperCase(), // BUY or SELL
          priceType: orderType.toUpperCase(), // LIMIT/MARKET/VWAP
          price: priceNum,
          volume: quantityNum,
          strategyName: '手动交易',
          orderRemark: getOrderRemark(tradeType, stockCode),
          accountId,
        });
        const orderResult = result.data?.placeOrder;

        if (result.error || orderResult?.success === false) {
          toast({
            title: '交易失败',
            description:
              result.error?.message ||
              orderResult?.message ||
              '请检查输入信息后重试',
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
    [accountId, blockedReasons, canTrade, createOrder, toast, onSuccess]
  );

  return useMemo(
    () => ({
      handleSubmit,
      isSubmitting,
    }),
    [handleSubmit, isSubmitting]
  );
}
