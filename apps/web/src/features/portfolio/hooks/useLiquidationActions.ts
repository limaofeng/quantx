import { useCallback, useMemo, useState } from 'react';
import { useMutation } from 'urql';

import { useCurrentAccount } from '@/features/dashboard/hooks';

import {
  LiquidatePositionsMutation,
} from './usePortfolio';

export type LiquidationCompletionStrategy =
  | 'AVAILABLE_NOW'
  | 'UNTIL_SNAPSHOT_CLEARED';
export type LiquidationConflictStrategy =
  | 'UNALLOCATED_ONLY'
  | 'REPLACE_CANCELLABLE';

export interface LiquidationExecutionOptions {
  completionStrategy: LiquidationCompletionStrategy;
  conflictStrategy: LiquidationConflictStrategy;
  executionMode: 'paper' | 'live';
  autoExitAuthorized?: boolean;
}

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
  liquidateAll: (
    options: LiquidationExecutionOptions
  ) => Promise<LiquidationActionResult>;
  liquidateMultiple: (
    stockCodes: string[],
    options: LiquidationExecutionOptions
  ) => Promise<LiquidationActionResult>;
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

  const [liquidationResult, executeLiquidatePositions] = useMutation(
    LiquidatePositionsMutation
  );

  const liquidateMultiple = useCallback(
    async (
      stockCodes: string[],
      options: LiquidationExecutionOptions
    ): Promise<LiquidationActionResult> => {
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
        const operation = await executeLiquidatePositions({
          input: {
            accountId,
            autoExitAuthorized: Boolean(options.autoExitAuthorized),
            completionStrategy: options.completionStrategy,
            conflictStrategy: options.conflictStrategy,
            confirm: true,
            executionMode: options.executionMode,
            instrumentCodes: codes,
            scope: 'SELECTED',
          },
        });
        if (operation.error) throw asActionError(operation.error.message);
        const group = operation.data?.liquidatePositions;
        for (const item of group?.plans ?? []) {
          if (!item.success) {
            failures.push({
              error: item.error || '计划创建失败',
              stockCode: item.instrumentCode,
            });
          } else if (item.planId) {
            submittedOrderIds.push(item.planId);
          }
        }

        const failureSummary = summarizeFailures(failures);
        const actionResult = {
          failures,
          message:
            failures.length > 0
              ? `部分清仓委托提交失败：${failureSummary}`
              : `已创建清仓计划：${codes.length} 只标的`,
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
    [accountId, executeLiquidatePositions]
  );

  const liquidateAll =
    useCallback(async (options: LiquidationExecutionOptions): Promise<LiquidationActionResult> => {
      setLocalLoading(true);
      setError(null);

      try {
        const operation = await executeLiquidatePositions({
          input: {
            accountId,
            autoExitAuthorized: Boolean(options.autoExitAuthorized),
            completionStrategy: options.completionStrategy,
            conflictStrategy: options.conflictStrategy,
            confirm: true,
            executionMode: options.executionMode,
            instrumentCodes: [],
            scope: 'ALL',
          },
        });

        if (operation.error) {
          const nextError = asActionError(operation.error.message);
          setError(nextError);
          throw nextError;
        }

        const result = operation.data?.liquidatePositions;
        if (!result) {
          const nextError = asActionError('清仓结果为空');
          setError(nextError);
          throw nextError;
        }

        const failures =
          result.plans?.filter(item => !item.success).map(item => ({
            error: item.error || '计划创建失败',
            stockCode: item.instrumentCode,
          })) || [];
        const actionResult = {
          failures,
          message: result.message,
          submittedOrderIds: (result.plans || [])
            .map(item => item.planId)
            .filter((value): value is string => Boolean(value)),
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
    }, [accountId, executeLiquidatePositions]);

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
        localLoading || liquidationResult.fetching,
      liquidateAll,
      liquidateMultiple,
      redeemCash,
    }),
    [
      error,
      liquidateAll,
      liquidateMultiple,
      localLoading,
      liquidationResult.fetching,
      redeemCash,
    ]
  );
}
