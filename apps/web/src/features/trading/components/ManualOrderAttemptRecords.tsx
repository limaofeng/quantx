import { Copy, ExternalLink, RefreshCw } from 'lucide-react';
import * as React from 'react';

import { StudioMenu, useStudioMenu } from '@/components/studio-workbench';
import { Button } from '@/components/ui/button';
import type {
  ManualOrderAttemptItem,
  ManualOrderAttemptsState,
} from '@/features/trading/hooks/useTrading';
import {
  getManualOrderPhasePresentation,
  manualOrderPriceTypeLabel,
} from '@/features/trading/manualOrderPresentation';
import { cn } from '@/utils/cn';

interface ManualOrderAttemptRecordsProps {
  highlightedClientOrderId?: string | null;
  onViewBrokerOrders?: (brokerOrderId: string) => void;
  state: ManualOrderAttemptsState;
}

function copyText(text: string) {
  if (!text || typeof navigator === 'undefined' || !navigator.clipboard) return;
  void navigator.clipboard.writeText(text);
}

function formatDateTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    month: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

function attemptPriceLabel(attempt: ManualOrderAttemptItem) {
  return attempt.orderType === 'FIX_PRICE'
    ? `${manualOrderPriceTypeLabel(attempt.orderType)} ${attempt.limitPrice}`
    : manualOrderPriceTypeLabel(attempt.orderType);
}

function AttemptPhaseBadge({ phase }: { phase: string }) {
  const presentation = getManualOrderPhasePresentation(phase);
  return (
    <span
      className={cn(
        'inline-flex max-w-full items-center rounded-md border px-1.5 py-0.5 text-ui-micro font-bold leading-4',
        presentation.classes.badge
      )}
    >
      {presentation.label}
    </span>
  );
}

function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div
      className="flex h-full min-h-40 flex-col items-center justify-center gap-2 border border-destructive/20 bg-destructive/5 p-ui-section text-center"
      role="alert"
    >
      <p className="text-ui-label font-bold text-destructive">
        下单请求列表加载失败
      </p>
      <p className="max-w-lg text-ui-caption text-destructive/80">{message}</p>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="mt-1 border-destructive/30 text-destructive hover:bg-destructive/10"
        onClick={onRetry}
      >
        <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
        重新加载
      </Button>
    </div>
  );
}

export function ManualOrderAttemptRecords({
  highlightedClientOrderId,
  onViewBrokerOrders,
  state,
}: ManualOrderAttemptRecordsProps) {
  const { closeMenu, menu, openAtPointer } =
    useStudioMenu<ManualOrderAttemptItem>();
  const errorMessage =
    state.error?.message?.trim() || '服务端没有返回可读的错误原因';

  if (state.loading && !state.hasSuccessfulData && !state.error) {
    return (
      <div className="flex h-full items-center justify-center text-ui-label text-muted-foreground/60">
        正在加载下单请求…
      </div>
    );
  }

  if (state.error && !state.hasSuccessfulData) {
    return <ErrorState message={errorMessage} onRetry={state.refresh} />;
  }

  const menuItems = [
    {
      icon: <Copy className="h-3.5 w-3.5" />,
      id: 'copy-client-order-id',
      label: '复制 clientOrderId',
      onSelect: () => copyText(menu?.payload?.clientOrderId || ''),
    },
    ...(menu?.payload?.brokerOrderId
      ? [
          {
            icon: <Copy className="h-3.5 w-3.5" />,
            id: 'copy-broker-order-id',
            label: '复制 brokerOrderId',
            onSelect: () => copyText(menu?.payload?.brokerOrderId || ''),
          },
        ]
      : []),
    {
      icon: <Copy className="h-3.5 w-3.5" />,
      id: 'copy-instrument-code',
      label: '复制证券代码',
      onSelect: () => copyText(menu?.payload?.instrumentCode || ''),
    },
  ];

  const menuView = (
    <StudioMenu
      ariaLabel="下单请求记录菜单"
      items={menuItems}
      menu={menu}
      onClose={closeMenu}
      width={196}
    />
  );

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-ui-caption">
          <span className="font-bold text-foreground">下单请求</span>
          <span className="rounded-full bg-blue-500/10 px-1.5 py-0.5 font-mono text-ui-micro font-bold text-blue-200">
            {state.totalCount}
          </span>
          {state.activeCount > 0 && (
            <span className="text-warning">活动 {state.activeCount}</span>
          )}
          {state.requiresAttentionCount > 0 && (
            <span className="text-destructive">
              需核对 {state.requiresAttentionCount}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span
            className="text-ui-micro text-muted-foreground/70"
            aria-live="polite"
          >
            {state.isRefreshing
              ? '正在刷新…'
              : state.lastUpdatedAt
                ? `更新于 ${formatDateTime(state.lastUpdatedAt)}`
                : ''}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-control-compact px-2 text-ui-caption text-blue-200 hover:bg-blue-500/10 hover:text-blue-100"
            onClick={state.refresh}
            disabled={state.loading}
            aria-label="刷新下单请求"
          >
            <RefreshCw
              className={cn(
                'mr-1.5 h-3.5 w-3.5',
                state.loading && 'animate-spin'
              )}
            />
            刷新
          </Button>
        </div>
      </div>

      {state.error && state.hasSuccessfulData && (
        <div
          className="flex shrink-0 items-center justify-between gap-2 border border-warning/25 bg-warning/5 px-2.5 py-2 text-ui-caption text-warning"
          role="alert"
        >
          <span>
            状态可能已过期：{errorMessage}
            {state.lastUpdatedAt
              ? `（上次成功更新于 ${formatDateTime(state.lastUpdatedAt)}）`
              : ''}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-control-compact shrink-0 px-2 text-ui-caption text-warning hover:bg-warning/10"
            onClick={state.refresh}
          >
            重新加载
          </Button>
        </div>
      )}

      {state.truncated && (
        <p className="shrink-0 text-ui-micro text-muted-foreground/70">
          仅显示前 {state.items.length} 条，共 {state.totalCount} 条下单请求
        </p>
      )}

      {state.items.length === 0 ? (
        <div className="flex min-h-40 flex-1 flex-col items-center justify-center gap-2 text-muted-foreground/60">
          <span className="text-ui-label font-medium">暂无下单请求</span>
          {state.loading && (
            <span className="text-ui-micro">正在从服务端刷新…</span>
          )}
        </div>
      ) : (
        <div
          className="min-h-0 flex-1 overflow-auto rounded-md border border-border/50 bg-muted/5 custom-scrollbar"
          aria-live="polite"
        >
          <table className="w-full min-w-[980px] border-collapse text-left">
            <thead className="sticky top-0 z-10 bg-muted/90 backdrop-blur">
              <tr>
                <th className="px-3 py-1.5 text-ui-micro font-bold text-muted-foreground">
                  入队时间
                </th>
                <th className="px-3 py-1.5 text-ui-micro font-bold text-muted-foreground">
                  证券 / 方向
                </th>
                <th className="px-3 py-1.5 text-ui-micro font-bold text-muted-foreground">
                  模式
                </th>
                <th className="px-3 py-1.5 text-ui-micro font-bold text-muted-foreground">
                  价格类型 / 价格
                </th>
                <th className="px-3 py-1.5 text-right text-ui-micro font-bold text-muted-foreground">
                  数量
                </th>
                <th className="px-3 py-1.5 text-ui-micro font-bold text-muted-foreground">
                  当前阶段
                </th>
                <th className="px-3 py-1.5 text-ui-micro font-bold text-muted-foreground">
                  最近更新 / 原因
                </th>
                <th className="px-3 py-1.5 text-ui-micro font-bold text-muted-foreground">
                  关联
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {state.items.map(attempt => {
                const presentation = getManualOrderPhasePresentation(
                  attempt.phase
                );
                const isHighlighted =
                  Boolean(highlightedClientOrderId) &&
                  attempt.clientOrderId === highlightedClientOrderId;
                const canViewBrokerOrder = Boolean(
                  attempt.brokerOrderId &&
                  presentation.phase !== 'RECONCILE_REQUIRED' &&
                  onViewBrokerOrders
                );
                return (
                  <tr
                    key={attempt.clientOrderId}
                    data-client-order-id={attempt.clientOrderId}
                    onContextMenu={event => openAtPointer(event, attempt)}
                    className={cn(
                      'group transition-colors hover:bg-blue-500/5',
                      isHighlighted &&
                        'bg-blue-500/10 ring-1 ring-inset ring-blue-400/50'
                    )}
                  >
                    <td className="px-3 py-2 align-top text-ui-micro font-mono tabular-nums text-muted-foreground">
                      {formatDateTime(attempt.createdAt)}
                    </td>
                    <td className="px-3 py-2 align-top">
                      <div className="flex items-center gap-1.5">
                        <span
                          className={cn(
                            'rounded-md px-1.5 py-0.5 text-ui-micro font-black',
                            attempt.side === 'BUY'
                              ? 'bg-market-up/10 text-market-up'
                              : 'bg-market-down/10 text-market-down'
                          )}
                        >
                          {attempt.side === 'BUY' ? '买入' : '卖出'}
                        </span>
                        <span className="font-mono text-ui-caption font-bold text-foreground">
                          {attempt.instrumentCode}
                        </span>
                      </div>
                      <div className="mt-1 max-w-56 truncate font-mono text-ui-micro text-muted-foreground/60">
                        {attempt.clientOrderId}
                      </div>
                    </td>
                    <td className="px-3 py-2 align-top text-ui-caption font-bold text-foreground/80">
                      {attempt.executionMode}
                    </td>
                    <td className="px-3 py-2 align-top text-ui-caption font-mono text-foreground/80">
                      {attemptPriceLabel(attempt)}
                    </td>
                    <td className="px-3 py-2 text-right align-top text-ui-caption font-mono font-bold tabular-nums text-foreground/80">
                      {attempt.volume.toLocaleString()}
                    </td>
                    <td className="max-w-64 px-3 py-2 align-top">
                      <AttemptPhaseBadge phase={attempt.phase} />
                      <p className="mt-1 max-w-64 text-ui-micro leading-4 text-muted-foreground">
                        {attempt.message}
                      </p>
                    </td>
                    <td className="max-w-64 px-3 py-2 align-top text-ui-micro text-muted-foreground">
                      <div className="font-mono tabular-nums">
                        {formatDateTime(attempt.updatedAt)}
                      </div>
                      {attempt.statusReason && (
                        <div className="mt-1 break-words text-muted-foreground/80">
                          原因：{attempt.statusReason}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 align-top">
                      {canViewBrokerOrder ? (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-control-compact whitespace-nowrap px-2 text-ui-micro text-blue-200 hover:bg-blue-500/10 hover:text-blue-100"
                          onClick={() =>
                            onViewBrokerOrders?.(attempt.brokerOrderId || '')
                          }
                        >
                          <ExternalLink className="mr-1 h-3 w-3" />
                          查看券商委托
                        </Button>
                      ) : attempt.brokerOrderId ? (
                        <span className="text-ui-micro text-destructive">
                          已有关联，需核对
                        </span>
                      ) : (
                        <span className="text-ui-micro text-muted-foreground/60">
                          尚无券商委托
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {menuView}
    </div>
  );
}
