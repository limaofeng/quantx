import { Loader2 } from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { financialToneClass } from '@/shared/utils/financialColors';
import { cn } from '@/utils/cn';

import { batchStatusLabels, formatNumber, formatSignedPercent } from './utils';

export type TTradePositionBatch = {
  batchId: string;
  stockCode: string;
  strategyRunId: string;
  status: string;
  entryClientOrderId?: string | null;
  exitClientOrderId?: string | null;
  entryBrokerOrderId?: string | null;
  exitBrokerOrderId?: string | null;
  targetVolume: number;
  entryFilledVolume: number;
  entryAvgPrice: number;
  exitFilledVolume: number;
  exitAvgPrice: number;
  activeVolume: number;
  lastPrice: number;
  netProfit?: number | null;
  lastNetProfitPct: number;
  peakNetProfitPct: number;
  trailingFloorPct?: number | null;
  exitReason?: string | null;
  exceptionReason?: string | null;
};

const EXITING_STATUSES = new Set([
  'EXIT_TRIGGERED',
  'EXIT_SUBMITTED',
  'EXIT_PARTIAL',
  'EXIT_REJECTED',
  'RECONCILE_REQUIRED',
  'KILL_SWITCHED',
]);

const CANCELLABLE_STATUSES = new Set([
  'ENTRY_QUEUED',
  'ENTRY_SUBMITTED',
  'ENTRY_PARTIAL',
  'EXIT_TRIGGERED',
  'EXIT_SUBMITTED',
  'EXIT_PARTIAL',
]);

function partitionTTradePositionBatches(
  batches: readonly TTradePositionBatch[],
  mode: 'LIVE' | 'REPLAY'
) {
  const active = batches.filter(
    batch =>
      batch.activeVolume > 0 &&
      !['CLOSED', 'COMPLETED', 'KILL_SWITCHED'].includes(batch.status)
  );
  const secondary =
    mode === 'LIVE'
      ? batches.filter(batch => EXITING_STATUSES.has(batch.status))
      : batches.filter(batch => !active.includes(batch));
  return { active, secondary };
}

function BatchTable({
  actionLoading,
  exitView,
  instrumentNames,
  loading,
  mode,
  onCancelOrder,
  rows,
}: {
  actionLoading: boolean;
  exitView: boolean;
  instrumentNames: ReadonlyMap<string, string>;
  loading: boolean;
  mode: 'LIVE' | 'REPLAY';
  onCancelOrder?: (clientOrderId: string) => void;
  rows: readonly TTradePositionBatch[];
}) {
  const replay = mode === 'REPLAY';
  return (
    <div className="min-h-0 overflow-auto custom-scrollbar">
      <table className="w-full min-w-[1040px] text-left text-ui-label">
        <thead className="sticky top-0 z-10 bg-[#0b1628] text-ui-caption font-black uppercase tracking-[0.08em] text-slate-600">
          <tr>
            <th className="px-ui-section py-2.5">标的 / 批次</th>
            <th className="px-3 py-2.5">生命周期</th>
            <th className="px-3 py-2.5 text-right">买入成交</th>
            <th className="px-3 py-2.5 text-right">活跃仓</th>
            <th className="px-3 py-2.5 text-right">
              买入均价 / {replay ? '退出价' : '最新价'}
            </th>
            <th className="px-3 py-2.5 text-right">
              {replay ? '净收益 / 收益率' : '净收益 / 峰值'}
            </th>
            <th className="px-3 py-2.5 text-right">
              {exitView ? '卖出成交 / 剩余' : '保护线'}
            </th>
            <th className="px-ui-section py-2.5 text-right">
              {replay ? '回放事实' : '委托 / 操作'}
            </th>
          </tr>
        </thead>
        <tbody>
          {loading && rows.length === 0 ? (
            <tr>
              <td
                colSpan={8}
                className="px-ui-section py-ui-empty text-center text-ui-label text-slate-600"
                role="status"
              >
                <Loader2
                  className="mr-2 inline-block h-4 w-4 animate-spin motion-reduce:animate-none"
                  aria-hidden="true"
                />
                正在读取做 T 批次…
              </td>
            </tr>
          ) : rows.length === 0 ? (
            <tr>
              <td
                colSpan={8}
                className="px-ui-section py-ui-empty text-center text-ui-label text-slate-600"
              >
                {exitView
                  ? replay
                    ? '本次回放没有已结束或清算异常批次'
                    : '当前没有待卖出或退出异常批次'
                  : replay
                    ? '本次回放没有未平做 T 仓位'
                    : '当前没有做 T 活跃仓位'}
              </td>
            </tr>
          ) : (
            rows.map(batch => {
              const clientOrderId = exitView
                ? batch.exitClientOrderId
                : batch.entryClientOrderId;
              const brokerOrderId = exitView
                ? batch.exitBrokerOrderId
                : batch.entryBrokerOrderId;
              const canCancel =
                mode === 'LIVE' &&
                Boolean(clientOrderId) &&
                CANCELLABLE_STATUSES.has(batch.status);
              const comparisonPrice = replay
                ? batch.exitAvgPrice || batch.lastPrice
                : batch.lastPrice;
              return (
                <tr
                  key={`${exitView ? 'exit' : 'open'}-${batch.batchId}`}
                  className="border-b border-white/[0.04] hover:bg-white/[0.025]"
                >
                  <td className="px-ui-section py-3">
                    <div className="font-black text-slate-100">
                      {instrumentNames.get(batch.stockCode.toUpperCase()) ||
                        batch.stockCode}
                    </div>
                    <div className="mt-1 font-mono text-ui-micro text-slate-600">
                      {batch.stockCode} · {batch.batchId.slice(0, 12)}
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    <span className="inline-flex border border-white/10 bg-white/[0.04] px-2 py-1 text-ui-caption font-bold text-slate-300">
                      {batchStatusLabels[batch.status] || batch.status}
                    </span>
                    {(batch.exitReason || batch.exceptionReason) && (
                      <div className="mt-1 max-w-52 text-ui-micro leading-4 text-amber-200/80">
                        {batch.exceptionReason || batch.exitReason}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-3 text-right font-mono tabular-nums text-slate-300">
                    {batch.entryFilledVolume.toLocaleString()} /{' '}
                    {batch.targetVolume.toLocaleString()}
                  </td>
                  <td className="px-3 py-3 text-right font-mono font-black tabular-nums text-cyan-200">
                    {batch.activeVolume.toLocaleString()}
                  </td>
                  <td className="px-3 py-3 text-right font-mono tabular-nums text-slate-300">
                    {formatNumber(batch.entryAvgPrice, 3)}
                    <span className="mx-1 text-slate-700">/</span>
                    {formatNumber(comparisonPrice, 3)}
                  </td>
                  <td
                    className={cn(
                      'px-3 py-3 text-right font-mono tabular-nums',
                      financialToneClass(batch.lastNetProfitPct, 'holding')
                    )}
                  >
                    {replay && batch.netProfit != null
                      ? `${batch.netProfit >= 0 ? '+' : ''}¥${formatNumber(
                          batch.netProfit,
                          2
                        )}`
                      : formatSignedPercent(batch.lastNetProfitPct)}
                    <div className="mt-1 text-ui-micro text-slate-600">
                      {replay
                        ? `收益率 ${formatSignedPercent(batch.lastNetProfitPct)}`
                        : `峰值 ${formatSignedPercent(batch.peakNetProfitPct)}`}
                    </div>
                  </td>
                  <td className="px-3 py-3 text-right font-mono tabular-nums text-slate-400">
                    {exitView ? (
                      <>
                        {batch.exitFilledVolume.toLocaleString()} /{' '}
                        {batch.activeVolume.toLocaleString()}
                      </>
                    ) : batch.trailingFloorPct == null ? (
                      replay ? (
                        '历史模拟'
                      ) : (
                        '未武装'
                      )
                    ) : (
                      formatSignedPercent(batch.trailingFloorPct)
                    )}
                  </td>
                  <td className="px-ui-section py-3 text-right">
                    <div className="font-mono text-ui-micro text-slate-600">
                      {replay
                        ? 'BACKTEST_BROKER'
                        : brokerOrderId ||
                          clientOrderId?.slice(0, 12) ||
                          '尚未委托'}
                    </div>
                    {canCancel && clientOrderId && onCancelOrder && (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={actionLoading}
                        onClick={() => onCancelOrder(clientOrderId)}
                        className="mt-1.5 h-7 rounded-sm border-white/10 px-2 text-ui-micro"
                      >
                        申请撤单
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

export function TTradePositionsView({
  actionLoading = false,
  batches,
  error,
  hasMore = false,
  instrumentNames,
  loading,
  loadingMore = false,
  mode,
  onCancelOrder,
  onLoadMore,
  onRefresh,
}: {
  actionLoading?: boolean;
  batches: readonly TTradePositionBatch[];
  error?: string | null;
  hasMore?: boolean;
  instrumentNames: ReadonlyMap<string, string>;
  loading: boolean;
  loadingMore?: boolean;
  mode: 'LIVE' | 'REPLAY';
  onCancelOrder?: (clientOrderId: string) => void;
  onLoadMore?: () => void;
  onRefresh: () => void;
}) {
  const replay = mode === 'REPLAY';
  const { active, secondary } = React.useMemo(
    () => partitionTTradePositionBatches(batches, mode),
    [batches, mode]
  );

  return (
    <div className="studio-workspace-surface flex h-full min-h-0 flex-col">
      {error && (
        <div
          role="alert"
          className="flex shrink-0 items-start justify-between gap-3 border-b border-rose-400/20 bg-rose-400/[0.06] px-ui-section py-2.5 text-ui-caption leading-4 text-rose-100"
        >
          <span>
            {replay
              ? '回放批次读取失败；未使用实盘批次作为回退。'
              : '做 T 批次读取失败；仍显示上次成功读取的结果。'}
          </span>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-6 shrink-0 px-2 text-ui-micro text-rose-100"
            onClick={onRefresh}
          >
            重试
          </Button>
        </div>
      )}
      {!error && loading && batches.length > 0 && (
        <div
          role="status"
          aria-busy="true"
          className="flex shrink-0 items-center gap-2 border-b border-cyan-400/15 bg-cyan-400/[0.04] px-ui-section py-2 text-ui-micro text-cyan-100"
        >
          <Loader2
            className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
            aria-hidden="true"
          />
          正在刷新{replay ? '回放' : '做 T'}批次，暂保留上次结果…
        </div>
      )}
      {replay && (
        <div className="shrink-0 border-b border-blue-400/15 bg-blue-400/[0.04] px-ui-section py-2 text-ui-micro text-blue-100">
          隔离回放 · 仓位来自 BACKTEST_BROKER，不读取当前实盘账户
        </div>
      )}
      <div className="grid shrink-0 grid-cols-2 border-b border-white/[0.05]">
        <div className="border-r border-white/[0.05] px-ui-section py-3">
          <div className="text-ui-caption font-black uppercase tracking-[0.1em] text-slate-600">
            {replay ? '期末未平批次' : '做 T 活跃仓位'}
          </div>
          <div className="mt-1 font-mono text-ui-page-title font-black text-cyan-200">
            {active.length}
          </div>
        </div>
        <div className="px-ui-section py-3">
          <div className="text-ui-caption font-black uppercase tracking-[0.1em] text-slate-600">
            {replay ? '已结束 / 清算批次' : '待卖出 / 退出中'}
          </div>
          <div className="mt-1 font-mono text-ui-page-title font-black text-amber-200">
            {secondary.length}
          </div>
        </div>
      </div>
      <section className="flex min-h-0 flex-1 flex-col border-b border-white/[0.05]">
        <h2 className="shrink-0 px-ui-section py-2.5 text-ui-label font-black text-slate-200">
          {replay ? '回放未平做 T 仓位' : '做 T 仓位'}
        </h2>
        <BatchTable
          actionLoading={actionLoading}
          exitView={false}
          instrumentNames={instrumentNames}
          loading={loading}
          mode={mode}
          onCancelOrder={onCancelOrder}
          rows={active}
        />
      </section>
      <section className="flex min-h-0 flex-1 flex-col">
        <h2 className="shrink-0 px-ui-section py-2.5 text-ui-label font-black text-slate-200">
          {replay ? '已结束与清算异常' : '待卖出与退出异常'}
        </h2>
        <BatchTable
          actionLoading={actionLoading}
          exitView
          instrumentNames={instrumentNames}
          loading={loading}
          mode={mode}
          onCancelOrder={onCancelOrder}
          rows={secondary}
        />
      </section>
      {hasMore && onLoadMore && (
        <div className="shrink-0 border-t border-white/[0.05] p-2 text-center">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={loadingMore}
            onClick={onLoadMore}
            className="h-control-compact text-ui-caption text-slate-400"
          >
            {loadingMore ? '加载中…' : '加载更多批次'}
          </Button>
        </div>
      )}
    </div>
  );
}
