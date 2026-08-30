import { useCallback, useMemo, useState } from 'react';
import { useMutation } from 'urql';

import {
  type AppDialogContextValue,
  useAppDialog,
} from '@/components/ui/app-dialog-context';
import { useCurrentAccount } from '@/features/dashboard/hooks';
import {
  LiquidationCompletionStrategy as LiquidationCompletionStrategyValue,
  LiquidationConflictStrategy as LiquidationConflictStrategyValue,
  LiquidationExecutionMode,
  LiquidationScope,
} from '@/generated/gql/graphql';

import {
  ConfirmLiquidationMutation,
  PreviewLiquidationMutation,
} from './usePortfolio';

export type LiquidationCompletionStrategy =
  | 'AVAILABLE_NOW'
  | 'UNTIL_SNAPSHOT_CLEARED';
export type LiquidationConflictStrategy =
  | 'UNALLOCATED_ONLY'
  | 'REPLACE_CANCELLABLE';

interface LiquidationPreviewSnapshot {
  challengeId: string;
  confirmationToken: string;
  includedCount: number;
  items: Array<{
    included: boolean;
    instrumentCode: string;
    instrumentName?: string | null;
    protectedVolume: number;
    reasonDetail: string;
  }>;
  skippedCount: number;
  warnings: string[];
}

export interface LiquidationExecutionOptions {
  completionStrategy: LiquidationCompletionStrategy;
  conflictStrategy: LiquidationConflictStrategy;
  executionMode: 'paper' | 'live';
  confirmPreview?: (
    preview: LiquidationPreviewSnapshot,
    message: string
  ) => boolean | Promise<boolean>;
}

export interface LiquidationActionFailure {
  error: string;
  stockCode: string;
}

export interface LiquidationActionResult {
  challengeId?: string | null;
  commandId?: string | null;
  failures: LiquidationActionFailure[];
  message: string;
  status?: string | null;
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
  return Array.from(new Set(stockCodes.map(normalizeStockCode))).filter(Boolean);
}

function asActionError(message: string) {
  return new Error(message || '清仓计划提交失败');
}

function idempotencyKey() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `liquidation-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function completionStrategyValue(value: LiquidationCompletionStrategy) {
  return value === 'AVAILABLE_NOW'
    ? LiquidationCompletionStrategyValue.AvailableNow
    : LiquidationCompletionStrategyValue.UntilSnapshotCleared;
}

function conflictStrategyValue(value: LiquidationConflictStrategy) {
  return value === 'REPLACE_CANCELLABLE'
    ? LiquidationConflictStrategyValue.ReplaceCancellable
    : LiquidationConflictStrategyValue.UnallocatedOnly;
}

function previewMessage(preview: LiquidationPreviewSnapshot) {
  const included = preview.items
    .filter(item => item.included)
    .map(
      item =>
        `${item.instrumentName || item.instrumentCode}：保护 ${item.protectedVolume} 股`
    );
  const skipped = preview.items
    .filter(item => !item.included)
    .map(item => `${item.instrumentCode}：${item.reasonDetail}`);
  return [
    `服务端预览：纳入 ${preview.includedCount} 只，跳过 ${preview.skippedCount} 只。`,
    ...included,
    ...(skipped.length ? ['跳过原因：', ...skipped] : []),
    ...(preview.warnings.length ? ['风险提示：', ...preview.warnings] : []),
    '确认后仅创建退出计划或排队命令，不代表已经委托或成交。',
  ].join('\n');
}

async function confirmServerPreview(
  preview: LiquidationPreviewSnapshot,
  options: LiquidationExecutionOptions,
  confirmDialog: AppDialogContextValue['confirm']
) {
  const message = previewMessage(preview);
  if (options.confirmPreview) {
    return Boolean(await options.confirmPreview(preview, message));
  }
  return confirmDialog({
    title: '确认清仓预览',
    description: message,
    confirmText: '确认提交',
    cancelText: '返回检查',
    variant: 'destructive',
  });
}

export function useLiquidationActions(): UseLiquidationActionsResult {
  const { confirm: confirmDialog } = useAppDialog();
  const [localLoading, setLocalLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const { data: accountData } = useCurrentAccount();
  const accountId = accountData?.currentAccount?.id;
  const [previewResult, executePreview] = useMutation(
    PreviewLiquidationMutation
  );
  const [confirmationResult, executeConfirmation] = useMutation(
    ConfirmLiquidationMutation
  );

  const execute = useCallback(
    async (
      scope: LiquidationScope,
      stockCodes: string[],
      options: LiquidationExecutionOptions
    ): Promise<LiquidationActionResult> => {
      if (!accountId) throw asActionError('当前账户不可用');
      const codes = uniqueStockCodes(stockCodes);
      if (scope === LiquidationScope.Selected && codes.length === 0) {
        return {
          failures: [],
          message: '没有可提交的清仓标的',
          submittedOrderIds: [],
          success: true,
        };
      }

      setLocalLoading(true);
      setError(null);
      try {
        const previewOperation = await executePreview({
          input: {
            accountId,
            completionStrategy: completionStrategyValue(
              options.completionStrategy
            ),
            conflictStrategy: conflictStrategyValue(options.conflictStrategy),
            executionMode:
              options.executionMode === 'live'
                ? LiquidationExecutionMode.Live
                : LiquidationExecutionMode.Paper,
            idempotencyKey: idempotencyKey(),
            instrumentCodes: scope === LiquidationScope.All ? [] : codes,
            scope,
          },
        });
        if (previewOperation.error) {
          throw asActionError(previewOperation.error.message);
        }
        const previewPayload = previewOperation.data?.previewLiquidation;
        const preview = previewPayload?.preview;
        if (!previewPayload?.success || !preview) {
          throw asActionError(previewPayload?.message || '清仓预览失败');
        }

        const confirmed = await confirmServerPreview(
          preview,
          options,
          confirmDialog
        );
        if (!confirmed) {
          return {
            challengeId: preview.challengeId,
            failures: [],
            message: '已取消本次清仓确认',
            status: 'CANCELLED_BY_USER',
            submittedOrderIds: [],
            success: false,
          };
        }

        const confirmationOperation = await executeConfirmation({
          input: {
            challengeId: preview.challengeId,
            confirmationToken: preview.confirmationToken,
          },
        });
        if (confirmationOperation.error) {
          throw asActionError(confirmationOperation.error.message);
        }
        const result = confirmationOperation.data?.confirmLiquidation;
        if (!result) throw asActionError('清仓确认结果为空');

        const failures = result.plans
          .filter(item => !item.success)
          .map(item => ({
            error: item.error || '计划创建失败',
            stockCode: item.instrumentCode,
          }));
        return {
          challengeId: result.challengeId,
          commandId: result.commandId,
          failures,
          message:
            result.status === 'QUEUED'
              ? `${result.message}；命令已排队，实际成交请以成交回报为准`
              : result.message,
          status: result.status,
          submittedOrderIds: result.plans
            .map(item => item.planId)
            .filter((value): value is string => Boolean(value)),
          success: Boolean(result.success),
        };
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
    [accountId, confirmDialog, executeConfirmation, executePreview]
  );

  const liquidateMultiple = useCallback(
    (stockCodes: string[], options: LiquidationExecutionOptions) =>
      execute(LiquidationScope.Selected, stockCodes, options),
    [execute]
  );
  const liquidateAll = useCallback(
    (options: LiquidationExecutionOptions) =>
      execute(LiquidationScope.All, [], options),
    [execute]
  );

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
        localLoading || previewResult.fetching || confirmationResult.fetching,
      liquidateAll,
      liquidateMultiple,
      redeemCash,
    }),
    [
      confirmationResult.fetching,
      error,
      liquidateAll,
      liquidateMultiple,
      localLoading,
      previewResult.fetching,
      redeemCash,
    ]
  );
}
