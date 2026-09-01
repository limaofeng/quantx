import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { useCurrentAccount } from '@/features/dashboard/hooks';
import type { ManualOrderType } from '@/features/trading/components/TradingCard/hooks/useFormState';
import {
  useConfirmManualOrder,
  useManualOrderCapabilities,
  usePreviewManualOrder,
} from '@/features/trading/hooks';
import type { ManualOrderAttemptItem } from '@/features/trading/hooks/useTrading';
import {
  ManualOrderExecutionMode,
  ManualOrderPriceType,
  ManualOrderSide,
  type Trading_PreviewManualOrderMutation,
} from '@/generated/gql/graphql';
import { useToast } from '@/hooks/use-toast';
import type { Stock } from '@/shared/types';
import { createClientId } from '@/utils/clientId';

export type ManualOrderPreviewTicket = NonNullable<
  Trading_PreviewManualOrderMutation['previewManualOrder']['preview']
>;

const TERMINAL_ATTEMPT_PHASES = new Set([
  'REJECTED_BEFORE_BROKER',
  'EXPIRED_BEFORE_BROKER',
  'CANCELLED_BEFORE_BROKER',
  'RECONCILE_REQUIRED',
]);

export interface UseTradingSubmitOptions {
  manualOrderAttempts?: ManualOrderAttemptItem[];
  refreshManualOrderAttempts?: () => void;
}

export interface TradingSubmitRequest {
  executionMode: ManualOrderExecutionMode;
  orderType: ManualOrderType;
  price: string;
  quantity: string;
  selectedStock: Stock | null;
  tradeType: 'buy' | 'sell';
}

function getSelectedStockCode(stock: Stock) {
  return String(stock.stockCode || stock.id || stock.code || '')
    .trim()
    .toUpperCase();
}

function errorMessage(error: unknown, fallback: string) {
  if (error && typeof error === 'object' && 'message' in error) {
    const message = String(error.message || '').trim();
    if (message) return message;
  }
  return fallback;
}

/**
 * Web 手工委托的两阶段提交逻辑：先生成服务器预览，再消费一次性挑战排队。
 */
export function useTradingSubmit(
  instrumentCode: string,
  onQueued?: (clientOrderId: string) => void,
  options: UseTradingSubmitOptions = {}
) {
  const { toast } = useToast();
  const { data: accountData } = useCurrentAccount();
  const accountId = accountData?.currentAccount?.id;
  const {
    capabilities,
    error: capabilitiesError,
    loading: capabilitiesLoading,
  } = useManualOrderCapabilities(accountId, instrumentCode);
  const { execute: executePreview, loading: previewLoading } =
    usePreviewManualOrder();
  const { execute: executeConfirm, loading: confirmLoading } =
    useConfirmManualOrder();
  const { manualOrderAttempts = [], refreshManualOrderAttempts } = options;
  const [trackedClientOrderId, setTrackedClientOrderId] = useState<
    string | null
  >(null);
  const orderAttempt = useMemo(
    () =>
      manualOrderAttempts.find(
        attempt =>
          attempt.clientOrderId === trackedClientOrderId &&
          String(attempt.instrumentCode).toUpperCase() ===
            String(instrumentCode).toUpperCase()
      ) || null,
    [instrumentCode, manualOrderAttempts, trackedClientOrderId]
  );
  const [preview, setPreview] = useState<ManualOrderPreviewTicket | null>(null);
  const [confirmationError, setConfirmationError] = useState('');
  const processingRef = useRef(false);
  const notifiedAttemptRef = useRef('');

  useEffect(() => {
    setPreview(null);
    setConfirmationError('');
    setTrackedClientOrderId(null);
    notifiedAttemptRef.current = '';
  }, [accountId, instrumentCode]);

  useEffect(() => {
    if (!orderAttempt || orderAttempt.clientOrderId !== trackedClientOrderId) {
      return;
    }
    const fingerprint = [
      orderAttempt.clientOrderId,
      orderAttempt.brokerOrderId || '',
      orderAttempt.phase,
      orderAttempt.statusReason || '',
    ].join(':');
    if (notifiedAttemptRef.current === fingerprint) return;

    if (orderAttempt.brokerOrderId) {
      notifiedAttemptRef.current = fingerprint;
      toast({
        title: '券商委托已生成',
        description: orderAttempt.message,
      });
      return;
    }
    const normalizedPhase = String(orderAttempt.phase || '').toUpperCase();
    if (TERMINAL_ATTEMPT_PHASES.has(normalizedPhase)) {
      notifiedAttemptRef.current = fingerprint;
      toast({
        title:
          normalizedPhase === 'RECONCILE_REQUIRED'
            ? '下单结果需要核对'
            : '未生成券商委托',
        description: orderAttempt.message,
        variant: 'destructive',
      });
    }
  }, [orderAttempt, toast, trackedClientOrderId]);

  const handleSubmit = useCallback(
    async (event: React.SyntheticEvent, request: TradingSubmitRequest) => {
      event.preventDefault();
      if (processingRef.current) return;

      if (!accountId) {
        toast({
          title: '账户不可用',
          description: '未连接唯一资金账户，无法生成委托预览',
          variant: 'destructive',
        });
        return;
      }

      const stockCode = request.selectedStock
        ? getSelectedStockCode(request.selectedStock)
        : '';
      const quantity = Number(request.quantity);
      const limitPrice = Number(request.price);
      const side =
        request.tradeType === 'buy'
          ? ManualOrderSide.Buy
          : ManualOrderSide.Sell;
      const priceType =
        request.orderType === 'best'
          ? ManualOrderPriceType.Best
          : ManualOrderPriceType.Limit;

      if (!/^\d{6}\.(SH|SZ|BJ)$/.test(stockCode)) {
        toast({
          title: '证券代码无效',
          description: '请选择带 SH、SZ 或 BJ 市场后缀的证券',
          variant: 'destructive',
        });
        return;
      }
      if (!Number.isInteger(quantity) || quantity <= 0) {
        toast({
          title: '委托数量无效',
          description: '请输入有效的正整数委托数量',
          variant: 'destructive',
        });
        return;
      }
      if (
        priceType === ManualOrderPriceType.Limit &&
        (!Number.isFinite(limitPrice) || limitPrice <= 0)
      ) {
        toast({
          title: '委托价格无效',
          description: '限价委托必须填写大于 0 的有效价格',
          variant: 'destructive',
        });
        return;
      }
      if (
        !capabilities ||
        capabilities.accountId !== accountId ||
        capabilities.instrumentCode !== stockCode ||
        !capabilities.canManualTrade ||
        !capabilities.supportedSides.includes(side) ||
        !capabilities.supportedPriceTypes.includes(priceType) ||
        !capabilities.executionModes.includes(request.executionMode) ||
        (request.executionMode === ManualOrderExecutionMode.Live &&
          !(side === ManualOrderSide.Buy
            ? capabilities.canLiveBuy
            : capabilities.canLiveSell))
      ) {
        toast({
          title: '当前委托能力不可用',
          description:
            capabilities?.liveBlockedReasons[0] ||
            '服务端尚未允许当前方向、报价方式或执行模式',
          variant: 'destructive',
        });
        return;
      }

      processingRef.current = true;
      setConfirmationError('');
      try {
        const result = await executePreview({
          input: {
            accountId,
            executionMode: request.executionMode,
            idempotencyKey: createClientId('manual-order'),
            instrumentCode: stockCode,
            limitPrice:
              priceType === ManualOrderPriceType.Limit ? limitPrice : undefined,
            priceType,
            side,
            volume: quantity,
          },
        });
        const payload = result.data?.previewManualOrder;
        if (result.error || !payload?.success || !payload.preview) {
          toast({
            title: '无法生成安全预览',
            description:
              payload?.message ||
              errorMessage(result.error, '服务端预览失败，请稍后重试'),
            variant: 'destructive',
          });
          return;
        }
        setPreview(payload.preview);
      } catch (error) {
        toast({
          title: '无法生成安全预览',
          description: errorMessage(error, '服务端预览失败，请稍后重试'),
          variant: 'destructive',
        });
      } finally {
        processingRef.current = false;
      }
    },
    [accountId, capabilities, executePreview, toast]
  );

  const confirmPreview = useCallback(async () => {
    if (!preview || processingRef.current) return false;
    if (Date.parse(preview.challengeExpiresAt) <= Date.now()) {
      setConfirmationError('确认票据已过期，请取消后重新获取服务器预览');
      return false;
    }

    processingRef.current = true;
    setConfirmationError('');
    try {
      const result = await executeConfirm({
        input: {
          challengeId: preview.challengeId,
          confirmationToken: preview.confirmationToken,
        },
      });
      const payload = result.data?.confirmManualOrder;
      if (result.error || !payload?.success) {
        setConfirmationError(
          payload?.message ||
            errorMessage(result.error, '委托确认失败，请重新获取预览')
        );
        return false;
      }

      setPreview(null);
      const clientOrderId = payload.clientOrderId || '';
      setTrackedClientOrderId(clientOrderId || null);
      refreshManualOrderAttempts?.();
      toast({
        title: '下单请求已进入队列',
        description:
          payload.message ||
          '尚未生成券商委托；正在等待 QMT Agent 下单前复核和券商回报',
      });
      if (clientOrderId) onQueued?.(clientOrderId);
      return true;
    } catch (error) {
      setConfirmationError(errorMessage(error, '委托确认失败，请重新获取预览'));
      return false;
    } finally {
      processingRef.current = false;
    }
  }, [executeConfirm, onQueued, preview, refreshManualOrderAttempts, toast]);

  const dismissPreview = useCallback(() => {
    if (processingRef.current) return;
    setPreview(null);
    setConfirmationError('');
  }, []);

  return useMemo(
    () => ({
      capabilities,
      capabilitiesError,
      capabilitiesLoading,
      confirmationError,
      confirmPreview,
      dismissPreview,
      handleSubmit,
      isConfirming: confirmLoading,
      isPreviewing: previewLoading,
      orderAttempt,
      preview,
      trackedClientOrderId,
    }),
    [
      capabilities,
      capabilitiesError,
      capabilitiesLoading,
      confirmationError,
      confirmLoading,
      confirmPreview,
      dismissPreview,
      handleSubmit,
      orderAttempt,
      preview,
      previewLoading,
      trackedClientOrderId,
    ]
  );
}
