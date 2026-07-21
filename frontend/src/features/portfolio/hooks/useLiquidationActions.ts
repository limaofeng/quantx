import { useCallback, useMemo, useState } from 'react';
import { useMutation } from 'urql';

import { useCurrentAccount } from '@/features/dashboard/hooks';

import {
  LiquidateAllPositionsMutation,
  LiquidatePositionMutation,
} from './usePortfolio';

export interface LiquidationActionFailure {
  error: string;
  stockCode: string;
}

export interface LiquidationActionResult {
  failures: LiquidationActionFailure[];
  message: string;
  submittedOrderIds: string[];
  success: boolean;
}

interface UseLiquidationActionsResult {
  error: Error | null;
  isLoading: boolean;
  liquidateAll: () => Promise<LiquidationActionResult>;
  liquidateMultiple: (stockCodes: string[]) => Promise<LiquidationActionResult>;
  redeemCash: (amount: number) => Promise<void>;
}

function normalizeStockCode(value: unknown) {
  return typeof value === 'string' ? value.trim().toUpperCase() : '';
}

function uniqueStockCodes(stockCodes: string[]) {
  return Array.from(new Set(stockCodes.map(normalizeStockCode))).filter(
    Boolean
  );
}

function asActionError(message: string) {
  return new Error(message || '清仓委托提交失败');
}

function summarizeFailures(failures: LiquidationActionFailure[]) {
  if (failures.length === 0) return '';
  return failures.map(item => `${item.stockCode}: ${item.error}`).join('; ');
}

export function useLiquidationActions(): UseLiquidationActionsResult {
  const [localLoading, setLocalLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const { data: accountData } = useCurrentAccount();
  const accountId = accountData?.currentAccount?.id;

  const [positionResult, executeLiquidatePosition] = useMutation(
    LiquidatePositionMutation
  );
  const [batchResult, executeLiquidateAll] = useMutation(
    LiquidateAllPositionsMutation
  );

  const liquidateMultiple = useCallback(
    async (stockCodes: string[]): Promise<LiquidationActionResult> => {
      const codes = uniqueStockCodes(stockCodes);
      if (codes.length === 0) {
        return {
          failures: [],
          message: '没有可提交的清仓标的',
          submittedOrderIds: [],
          success: true,
        };
      }

      setLocalLoading(true);
      setError(null);

      const failures: LiquidationActionFailure[] = [];
      const submittedOrderIds: string[] = [];

      try {
        for (const stockCode of codes) {
          const operation = await executeLiquidatePosition({
            input: {
              accountId,
              confirm: true,
              maxRetry: 1,
              stockCode,
            },
          });

          if (operation.error) {
            failures.push({
              error: operation.error.message,
              stockCode,
            });
            continue;
          }

          const result = operation.data?.liquidatePosition;
          if (!result?.success) {
            failures.push({
              error: result?.error || result?.message || '委托提交失败',
              stockCode,
            });
            continue;
          }

          if (result.orderId !== null && result.orderId !== undefined) {
            submittedOrderIds.push(String(result.orderId));
          }
        }

        const failureSummary = summarizeFailures(failures);
        const actionResult = {
          failures,
          message:
            failures.length > 0
              ? `部分清仓委托提交失败：${failureSummary}`
              : `清仓委托已提交：${codes.length} 只标的`,
          submittedOrderIds,
          success: failures.length === 0,
        };

        if (!actionResult.success)
          setError(asActionError(actionResult.message));
        return actionResult;
      } catch (nextError) {
        const normalized =
          nextError instanceof Error
            ? nextError
            : asActionError(String(nextError));
        setError(normalized);
        throw normalized;
      } finally {
        setLocalLoading(false);
      }
    },
    [accountId, executeLiquidatePosition]
  );

  const liquidateAll =
    useCallback(async (): Promise<LiquidationActionResult> => {
      setLocalLoading(true);
      setError(null);

      try {
        const operation = await executeLiquidateAll({
          input: {
            accountId,
            confirm: true,
            maxRetry: 1,
          },
        });

        if (operation.error) {
          const nextError = asActionError(operation.error.message);
          setError(nextError);
          throw nextError;
        }

        const result = operation.data?.liquidateAllPositions;
        if (!result) {
          const nextError = asActionError('清仓结果为空');
          setError(nextError);
          throw nextError;
        }

        const failures =
          result.errors?.map(item => ({
            error: item.error,
            stockCode: item.stockCode,
          })) || [];
        const actionResult = {
          failures,
          message: result.message,
          submittedOrderIds: (result.orders || []).map(String),
          success: Boolean(result.success),
        };

        if (!actionResult.success)
          setError(asActionError(actionResult.message));
        return actionResult;
      } catch (nextError) {
        const normalized =
          nextError instanceof Error
            ? nextError
            : asActionError(String(nextError));
        setError(normalized);
        throw normalized;
      } finally {
        setLocalLoading(false);
      }
    }, [accountId, executeLiquidateAll]);

  const redeemCash = useCallback(async () => {
    const nextError = new Error(
      '资金赎回请在券商客户端办理，QuantX 当前不提交转账指令。'
    );
    setError(nextError);
    throw nextError;
  }, []);

  return useMemo(
    () => ({
      error,
      isLoading:
        localLoading || positionResult.fetching || batchResult.fetching,
      liquidateAll,
      liquidateMultiple,
      redeemCash,
    }),
    [
      batchResult.fetching,
      error,
      liquidateAll,
      liquidateMultiple,
      localLoading,
      positionResult.fetching,
      redeemCash,
    ]
  );
}
