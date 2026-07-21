import { BarChart3, ClipboardList, RefreshCw, Wallet } from 'lucide-react';
import * as React from 'react';

import type {
  PortfolioSummaryData,
  Position,
} from '@/features/portfolio/types';
import { useStockDisclosures } from '@/features/stocks/hooks';
import { cn } from '@/utils/cn';

import { DisclosureTimeline } from './DisclosureTimeline';
import {
  formatCompactCurrency,
  formatDate,
  formatDateTime,
  formatPercent,
  formatPrice,
  formatShares,
  formatSignedCurrency,
  getProgressPercent,
  getToneClass,
  toFiniteNumber,
} from './formatters';
import { RepurchaseBrief } from './RepurchaseBrief';

function MetricCard({
  label,
  subValue,
  toneValue,
  value,
}: {
  label: string;
  subValue?: string;
  toneValue?: unknown;
  value: string;
}) {
  return (
    <div className="min-w-0 border border-white/5 bg-white/[0.03] px-3 py-2.5">
      <div className="truncate text-[10px] font-bold text-slate-500">
        {label}
      </div>
      <div
        className={cn(
          'mt-1 truncate font-mono text-sm font-black tabular-nums',
          toneValue === undefined ? 'text-slate-100' : getToneClass(toneValue)
        )}
      >
        {value}
      </div>
      {subValue && (
        <div className="mt-1 truncate text-[10px] font-bold text-slate-600">
          {subValue}
        </div>
      )}
    </div>
  );
}

function DetailPanel({
  children,
  icon: Icon,
  title,
}: {
  children: React.ReactNode;
  icon: React.ElementType;
  title: string;
}) {
  return (
    <section className="min-w-0 border border-white/5 bg-[#0b1120]/70">
      <div className="flex h-10 items-center gap-2 border-b border-white/5 px-3">
        <Icon className="h-3.5 w-3.5 text-red-300" />
        <h3 className="truncate text-xs font-black text-slate-200">{title}</h3>
      </div>
      <div className="p-3">{children}</div>
    </section>
  );
}

function MiniRow({
  label,
  toneValue,
  value,
}: {
  label: string;
  toneValue?: unknown;
  value: string;
}) {
  return (
    <div className="min-w-0 border border-white/5 bg-[#08101d]/80 px-3 py-2">
      <div className="truncate text-[10px] font-bold text-slate-500">
        {label}
      </div>
      <div
        className={cn(
          'mt-1 truncate font-mono text-xs font-black',
          toneValue === undefined ? 'text-slate-200' : getToneClass(toneValue)
        )}
      >
        {value}
      </div>
    </div>
  );
}

export function StockDetailWorkbench({
  accountName,
  accountType,
  activeModeLabel,
  activeOrderCount,
  cash,
  changePercent,
  displayName,
  frozenCash,
  hasActiveOrders,
  holding,
  lastPrice,
  layoutLabel,
  onOpenStockInfo,
  portfolioSummary,
  stockCode,
  totalAsset,
}: {
  accountName: string;
  accountType?: string | null;
  activeModeLabel: string;
  activeOrderCount: number;
  cash?: unknown;
  changePercent?: unknown;
  displayName: string;
  frozenCash?: unknown;
  hasActiveOrders: boolean;
  holding?: Position | null;
  lastPrice?: unknown;
  layoutLabel: string;
  onOpenStockInfo?: () => void;
  portfolioSummary?: PortfolioSummaryData;
  stockCode: string;
  totalAsset?: unknown;
}) {
  const {
    error,
    isLoading,
    isRefreshing,
    refresh,
    refreshError,
    refreshStatus,
    summary,
  } = useStockDisclosures(stockCode, 20);

  const volume = holding?.volume ?? null;
  const canUseVolume = holding?.canUseVolume ?? null;
  const frozenVolume = holding?.frozenVolume ?? null;
  const volumeNumber = toFiniteNumber(volume);
  const avgPriceNumber = toFiniteNumber(holding?.avgPrice);
  const costAmount =
    volumeNumber !== null && avgPriceNumber !== null
      ? volumeNumber * avgPriceNumber
      : null;
  const portfolioMarketValueNumber = toFiniteNumber(
    portfolioSummary?.totalMarketValue
  );
  const marketValueNumber = toFiniteNumber(holding?.marketValue);
  const marketValuePercent =
    holding?.marketValuePercent ??
    (marketValueNumber !== null &&
    portfolioMarketValueNumber !== null &&
    portfolioMarketValueNumber > 0
      ? (marketValueNumber / portfolioMarketValueNumber) * 100
      : null);
  const availablePercent = getProgressPercent(canUseVolume, volume);
  const frozenPercent = getProgressPercent(frozenVolume, volume);
  const latestAnnouncementDate =
    summary?.latestAnnouncementDate ||
    summary?.announcements?.[0]?.announceDate ||
    null;
  const sourceMessage =
    refreshError?.message ||
    refreshStatus?.errorMessage ||
    refreshStatus?.message ||
    error?.message ||
    summary?.sourceMessage ||
    null;

  const summaryMetrics = [
    {
      label: '最新价',
      subValue: `涨跌幅 ${formatPercent(changePercent)}`,
      toneValue: changePercent,
      value: formatPrice(lastPrice),
    },
    {
      label: '持仓市值',
      subValue: `组合占比 ${formatPercent(marketValuePercent, false)}`,
      value: formatCompactCurrency(holding?.marketValue),
    },
    {
      label: '浮动盈亏',
      subValue: `收益率 ${formatPercent(holding?.profitRate)}`,
      toneValue: holding?.profitLoss ?? holding?.profitRate,
      value: formatSignedCurrency(holding?.profitLoss),
    },
    {
      label: '最新公告',
      subValue: summary?.sourceStatus || 'DISCLOSURE',
      value: formatDate(latestAnnouncementDate),
    },
  ];

  const holdingRows = [
    { label: '证券代码', value: stockCode || '--' },
    { label: '持仓数量', value: `${formatShares(volume)} 股` },
    { label: '可用数量', value: `${formatShares(canUseVolume)} 股` },
    { label: '冻结数量', value: `${formatShares(frozenVolume)} 股` },
    { label: '在途数量', value: `${formatShares(holding?.onRoadVolume)} 股` },
    {
      label: '昨日持仓',
      value: `${formatShares(holding?.yesterdayVolume)} 股`,
    },
    { label: '平均成本', value: formatPrice(holding?.avgPrice) },
    { label: '持仓成本', value: formatCompactCurrency(costAmount) },
    {
      label: '更新时间',
      value: formatDateTime(holding?.quoteTime ?? holding?.updatedAt),
    },
  ];

  const executionRows = [
    { label: '账户', value: accountName },
    { label: '总资产', value: formatCompactCurrency(totalAsset) },
    { label: '可用资金', value: formatCompactCurrency(cash) },
    { label: '冻结资金', value: formatCompactCurrency(frozenCash) },
    {
      label: '组合盈亏',
      toneValue: portfolioSummary?.totalProfitLoss,
      value: formatSignedCurrency(portfolioSummary?.totalProfitLoss),
    },
    {
      label: '组合收益率',
      toneValue: portfolioSummary?.totalProfitLossPercent,
      value: formatPercent(portfolioSummary?.totalProfitLossPercent),
    },
  ];

  return (
    <div className="border-t border-white/5 bg-[#08101d] px-4 py-4">
      <div className="mx-auto flex max-w-[1480px] flex-col gap-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[10px] font-black uppercase text-red-300">
              Detail
            </div>
            <div className="mt-1 flex min-w-0 flex-wrap items-center gap-2">
              <h2 className="truncate text-lg font-black text-slate-100">
                {displayName} 详情
              </h2>
              <span className="rounded border border-white/10 bg-white/[0.04] px-2 py-1 font-mono text-[10px] font-bold text-slate-500">
                {stockCode || '--'}
              </span>
              <span
                className={cn(
                  'rounded border px-2 py-1 text-[10px] font-black',
                  holding
                    ? 'border-red-500/25 bg-red-500/10 text-red-200'
                    : 'border-amber-400/20 bg-amber-500/10 text-amber-200'
                )}
              >
                {holding ? '已持仓' : '未持仓'}
              </span>
            </div>
          </div>

          <div className="flex min-w-0 flex-wrap items-center gap-2">
            {sourceMessage && (
              <span className="max-w-[360px] truncate text-[11px] font-bold text-slate-500">
                {sourceMessage}
              </span>
            )}
            <button
              type="button"
              disabled={!stockCode || isRefreshing}
              onClick={() => void refresh()}
              className="inline-flex h-8 items-center gap-2 rounded-md border border-white/10 px-3 text-xs font-bold text-slate-300 transition-colors hover:border-red-500/35 hover:bg-red-500/10 hover:text-red-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RefreshCw
                className={cn('h-3.5 w-3.5', isRefreshing && 'animate-spin')}
              />
              刷新公告
            </button>
            <button
              type="button"
              disabled={!stockCode}
              onClick={onOpenStockInfo}
              className="inline-flex h-8 items-center gap-2 rounded-md border border-white/10 px-3 text-xs font-bold text-slate-300 transition-colors hover:border-red-500/35 hover:bg-red-500/10 hover:text-red-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <BarChart3 className="h-3.5 w-3.5" />
              个股信息
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2 xl:grid-cols-4">
          {summaryMetrics.map(metric => (
            <MetricCard
              key={metric.label}
              label={metric.label}
              subValue={metric.subValue}
              toneValue={metric.toneValue}
              value={metric.value}
            />
          ))}
        </div>

        <div className="grid gap-3 xl:grid-cols-[minmax(0,1.45fr)_minmax(340px,0.55fr)]">
          <DisclosureTimeline
            announcements={summary?.announcements ?? []}
            isLoading={isLoading}
            sourceStatus={summary?.sourceStatus}
          />

          <div className="flex min-w-0 flex-col gap-3">
            <RepurchaseBrief
              event={summary?.repurchaseEvents?.[0] ?? null}
              sourceStatus={summary?.sourceStatus}
            />

            <DetailPanel icon={ClipboardList} title="持仓明细">
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
                {holdingRows.map(row => (
                  <MiniRow
                    key={row.label}
                    label={row.label}
                    value={row.value}
                  />
                ))}
              </div>

              <div className="mt-3 border-t border-white/5 pt-3">
                <div className="flex items-center justify-between gap-3 text-[10px] font-bold text-slate-500">
                  <span>可用库存</span>
                  <span className="font-mono text-slate-300">
                    {availablePercent.toFixed(0)}%
                  </span>
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                  <div
                    className="h-full rounded-full bg-red-400"
                    style={{ width: `${availablePercent}%` }}
                  />
                </div>
                <div className="mt-2 flex items-center justify-between gap-3 text-[10px] font-bold text-slate-600">
                  <span>冻结库存</span>
                  <span className="font-mono">{frozenPercent.toFixed(0)}%</span>
                </div>
              </div>
            </DetailPanel>

            <DetailPanel icon={Wallet} title="账户与执行">
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
                {executionRows.map(row => (
                  <MiniRow
                    key={row.label}
                    label={row.label}
                    toneValue={row.toneValue}
                    value={row.value}
                  />
                ))}
                <MiniRow label="当前模块" value={activeModeLabel} />
                <MiniRow label="盘口布局" value={layoutLabel} />
                <MiniRow
                  label="活跃委托"
                  toneValue={hasActiveOrders ? 1 : 0}
                  value={`${activeOrderCount} 笔`}
                />
                <MiniRow label="账户类型" value={accountType || '--'} />
              </div>
            </DetailPanel>
          </div>
        </div>
      </div>
    </div>
  );
}
