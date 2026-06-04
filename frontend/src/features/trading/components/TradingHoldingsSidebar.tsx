import {
  BriefcaseBusiness,
  CandlestickChart,
  ExternalLink,
  Loader2,
  RefreshCw,
  TrendingDown,
  TrendingUp,
  Wallet,
} from 'lucide-react';
import * as React from 'react';

import { StudioMenu, useStudioMenu } from '@/components/studio-workbench';
import { Button } from '@/components/ui/button';
import { useRealTimeHoldings } from '@/features/portfolio/hooks/useRealTimeHoldings';
import type {
  PortfolioSummaryData,
  Position,
} from '@/features/portfolio/types';
import { cn } from '@/utils/cn';
import { formatPercent } from '@/utils/transform/data';

interface TradingHoldingsSidebarProps {
  accountName: string;
  error?: unknown;
  holdings: Position[];
  isLoading: boolean;
  onAccountOpen: () => void;
  onHoldingOpenInNewWindow?: (holding: Position) => void;
  onHoldingSelect: (holding: Position) => void;
  onStockInfoOpen: (holding: Position) => void;
  onRefresh: () => void;
  portfolioSummary?: PortfolioSummaryData;
  selectedStockCode?: string;
  totalAsset?: number;
}

function normalizeStockCode(value: unknown) {
  return typeof value === 'string' ? value.trim().toUpperCase() : '';
}

function getStockIconText(name: string) {
  if (!name) return '--';
  if (name.length === 1) return name;
  return `${name.charAt(0)}${name.charAt(name.length - 1)}`;
}

function formatCompactCurrency(value?: number | null) {
  const amount = Number(value ?? 0);
  const abs = Math.abs(amount);
  const sign = amount < 0 ? '-' : '';

  if (abs >= 100000000) return `${sign}¥${(abs / 100000000).toFixed(2)}亿`;
  if (abs >= 10000) return `${sign}¥${(abs / 10000).toFixed(2)}万`;
  return `${sign}¥${abs.toFixed(2)}`;
}

function formatCompactCurrencyOrDash(value?: number | null) {
  const amount = toFiniteNumber(value);
  return amount === null ? '--' : formatCompactCurrency(amount);
}

function formatSignedCurrency(value?: number | null) {
  const amount = Number(value ?? 0);
  return `${amount >= 0 ? '+' : ''}${formatCompactCurrency(amount)}`;
}

function formatShares(value?: number | null) {
  return Number(value ?? 0).toLocaleString('zh-CN');
}

function toFiniteNumber(value: unknown) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatPercentOrDash(value: number | null) {
  return value === null ? '--' : formatPercent(value);
}

function HoldingsSkeleton() {
  return (
    <div className="space-y-2 px-2 py-1">
      {Array.from({ length: 5 }).map((_, index) => (
        <div
          key={index}
          className="h-[86px] animate-pulse rounded-md border border-white/5 bg-white/[0.035]"
        />
      ))}
    </div>
  );
}

function EmptyHoldingsState() {
  return (
    <div className="mx-2 mt-6 rounded-md border border-dashed border-white/10 bg-white/[0.025] px-4 py-6 text-center">
      <BriefcaseBusiness className="mx-auto h-5 w-5 text-slate-600" />
      <div className="mt-3 text-xs font-black text-slate-300">暂无持仓</div>
      <div className="mt-1 text-[10px] leading-relaxed text-slate-600">
        当前账户没有可展示的持仓股票。
      </div>
    </div>
  );
}

export function TradingHoldingsSidebar({
  accountName,
  error,
  holdings,
  isLoading,
  onAccountOpen,
  onHoldingOpenInNewWindow,
  onHoldingSelect,
  onStockInfoOpen,
  onRefresh,
  portfolioSummary,
  selectedStockCode,
  totalAsset,
}: TradingHoldingsSidebarProps) {
  const hasHoldings = holdings.length > 0;
  const { closeMenu, menu, openAtPointer } = useStudioMenu<Position>();
  const { holdings: realtimeHoldings } = useRealTimeHoldings({
    holdings,
    enabled: hasHoldings,
  });

  const normalizedSelectedStockCode = normalizeStockCode(selectedStockCode);
  const sortedHoldings = React.useMemo(
    () =>
      [...realtimeHoldings].sort(
        (left, right) => (right.marketValue ?? 0) - (left.marketValue ?? 0)
      ),
    [realtimeHoldings]
  );
  const totalMarketValue =
    portfolioSummary?.totalMarketValue ??
    sortedHoldings.reduce(
      (sum, holding) => sum + (holding.marketValue ?? 0),
      0
    );
  const displayTotalAsset = totalAsset ?? portfolioSummary?.totalAsset;
  const hasError = Boolean(error);

  return (
    <aside className="flex h-full min-h-0 flex-col">
      <div className="border-b border-white/5 px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[10px] font-black uppercase tracking-[0.24em] text-red-400">
              Holdings
            </div>
            <div className="mt-1 truncate text-sm font-black text-slate-100">
              持仓
            </div>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={onRefresh}
            className="h-8 w-8 shrink-0 rounded-md border border-white/10 text-slate-500 transition-colors hover:border-red-500/30 hover:bg-red-500/10 hover:text-red-200"
            aria-label="刷新持仓"
            title="刷新持仓"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2">
          <div className="rounded-md border border-white/5 bg-white/[0.03] px-2.5 py-2">
            <div className="text-[9px] font-black uppercase tracking-[0.18em] text-slate-600">
              总资产
            </div>
            <div className="mt-1 truncate font-mono text-[11px] font-black text-slate-200">
              {displayTotalAsset === undefined
                ? '读取中'
                : formatCompactCurrency(displayTotalAsset)}
            </div>
          </div>
          <div className="rounded-md border border-white/5 bg-white/[0.03] px-2.5 py-2">
            <div className="text-[9px] font-black uppercase tracking-[0.18em] text-slate-600">
              持仓市值
            </div>
            <div className="mt-1 truncate font-mono text-[11px] font-black text-slate-200">
              {formatCompactCurrency(totalMarketValue)}
            </div>
          </div>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex h-8 shrink-0 items-center justify-between border-b border-white/5 px-4">
          <span className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-600">
            持仓
          </span>
          <span className="font-mono text-[10px] font-bold text-slate-500">
            {sortedHoldings.length} 只
          </span>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto py-2 custom-scrollbar">
          {isLoading ? (
            <HoldingsSkeleton />
          ) : sortedHoldings.length === 0 ? (
            <EmptyHoldingsState />
          ) : (
            <div className="space-y-1.5 px-2">
              {sortedHoldings.map(holding => {
                const stockCode = normalizeStockCode(holding.stockCode);
                const stockName = holding.instrumentName || stockCode;
                const isSelected = stockCode === normalizedSelectedStockCode;
                const profitLoss = holding.profitLoss ?? 0;
                const averageCost = toFiniteNumber(holding.avgPrice);
                const volume = toFiniteNumber(holding.volume);
                const costAmount =
                  averageCost !== null && volume !== null
                    ? averageCost * volume
                    : null;
                const holdingReturnRate =
                  toFiniteNumber(holding.profitRate) ??
                  (costAmount !== null && costAmount > 0
                    ? (profitLoss / costAmount) * 100
                    : null);
                const dayChangePercent =
                  toFiniteNumber(holding.changePercent) ??
                  toFiniteNumber(holding.todayProfitRate);
                const isDayChangePositive =
                  dayChangePercent !== null && dayChangePercent >= 0;
                const isProfitLossPositive = profitLoss >= 0;
                const isHoldingReturnPositive =
                  holdingReturnRate === null
                    ? isProfitLossPositive
                    : holdingReturnRate >= 0;
                const ToneIcon =
                  dayChangePercent === null
                    ? null
                    : isDayChangePositive
                      ? TrendingUp
                      : TrendingDown;
                const metricRows = [
                  [
                    {
                      label: '数量',
                      value: formatShares(holding.volume),
                      valueClassName: 'text-slate-300',
                    },
                    {
                      label: '可用',
                      value: formatShares(holding.canUseVolume),
                      valueClassName: 'text-slate-300',
                    },
                    {
                      label: '持有收益',
                      value: formatPercentOrDash(holdingReturnRate),
                      valueClassName: isHoldingReturnPositive
                        ? 'text-rose-300'
                        : 'text-emerald-300',
                    },
                  ],
                  [
                    {
                      label: '成本额',
                      value: formatCompactCurrencyOrDash(costAmount),
                      valueClassName: 'text-slate-300',
                    },
                    {
                      label: '市值',
                      value: formatCompactCurrencyOrDash(holding.marketValue),
                      valueClassName: 'text-slate-300',
                    },
                    {
                      label: '盈亏',
                      value: formatSignedCurrency(profitLoss),
                      valueClassName: isProfitLossPositive
                        ? 'text-rose-300'
                        : 'text-emerald-300',
                    },
                  ],
                ];

                return (
                  <button
                    key={holding.id}
                    type="button"
                    onClick={() => onHoldingSelect(holding)}
                    onContextMenu={event => openAtPointer(event, holding)}
                    className={cn(
                      'group w-full rounded-md border px-2.5 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70',
                      isSelected
                        ? 'border-red-500/35 bg-red-500/10 text-red-100'
                        : 'border-white/5 bg-white/[0.025] text-slate-300 hover:border-red-500/25 hover:bg-white/[0.055] hover:text-slate-100'
                    )}
                    aria-current={isSelected ? 'true' : undefined}
                  >
                    <div className="flex min-w-0 items-start gap-2.5">
                      <div
                        className={cn(
                          'flex h-8 w-8 shrink-0 items-center justify-center rounded-md border text-[10px] font-black',
                          isSelected
                            ? 'border-red-500/30 bg-red-500/15 text-red-200'
                            : 'border-white/10 bg-[#08101d] text-slate-400 group-hover:border-red-500/25 group-hover:text-red-200'
                        )}
                      >
                        {getStockIconText(stockName)}
                      </div>
                      <div className="flex min-w-0 flex-1 items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <span className="block min-w-0 truncate text-xs font-black">
                            {stockName}
                          </span>
                          <div className="mt-0.5 flex min-w-0 items-center gap-1.5">
                            <span className="truncate font-mono text-[10px] font-bold text-slate-600">
                              {stockCode}
                            </span>
                          </div>
                        </div>
                        <div className="shrink-0 space-y-1 text-right font-mono leading-none">
                          <div
                            className={cn(
                              'inline-flex items-center justify-end gap-1 font-mono text-[10px] font-black leading-none',
                              dayChangePercent === null
                                ? 'text-slate-500'
                                : isDayChangePositive
                                  ? 'text-rose-300'
                                  : 'text-emerald-300'
                            )}
                          >
                            {ToneIcon && <ToneIcon className="h-3 w-3" />}
                            {formatPercentOrDash(dayChangePercent)}
                          </div>
                          <div className="flex items-baseline justify-end gap-2">
                            <span className="inline-flex items-baseline gap-1">
                              <span className="text-[8px] font-black leading-none text-slate-600">
                                现价
                              </span>
                              <span className="text-[9px] font-black leading-none text-slate-300">
                                {formatCompactCurrencyOrDash(holding.lastPrice)}
                              </span>
                            </span>
                            <span className="inline-flex items-baseline gap-1">
                              <span className="text-[8px] font-black leading-none text-slate-600">
                                成本
                              </span>
                              <span className="text-[9px] font-bold leading-none text-slate-500">
                                {formatCompactCurrencyOrDash(holding.avgPrice)}
                              </span>
                            </span>
                          </div>
                        </div>
                      </div>
                    </div>

                    <div className="mt-2 rounded border border-white/5 bg-[#08101d]/70 px-2 py-1.5">
                      <div className="space-y-1">
                        {metricRows.map((metrics, rowIndex) => (
                          <div
                            key={rowIndex}
                            className="grid grid-cols-[max-content_max-content_max-content] items-baseline justify-between gap-x-2"
                          >
                            {metrics.map(metric => (
                              <div
                                key={metric.label}
                                className="flex min-w-0 items-baseline gap-1 leading-none"
                              >
                                <span className="shrink-0 text-[8px] font-black leading-none tracking-wider text-slate-600">
                                  {metric.label}
                                </span>
                                <span
                                  className={cn(
                                    'min-w-0 max-w-[4.75rem] truncate font-mono text-[10px] font-bold leading-none',
                                    metric.valueClassName
                                  )}
                                >
                                  {metric.value}
                                </span>
                              </div>
                            ))}
                          </div>
                        ))}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}

          {hasError && (
            <div className="mx-2 mt-2 rounded-md border border-amber-400/20 bg-amber-500/10 px-3 py-2 text-[10px] font-bold leading-relaxed text-amber-200/80">
              持仓数据读取异常，已保留当前可用数据。
            </div>
          )}
        </div>
      </div>

      <div className="border-t border-white/5 p-3">
        <button
          type="button"
          onClick={onAccountOpen}
          className="flex w-full items-center gap-3 rounded-md border border-white/10 px-2.5 py-2 text-left text-slate-400 transition-colors hover:border-red-500/30 hover:bg-red-500/10 hover:text-red-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70"
        >
          <Wallet className="h-4 w-4 shrink-0" />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-xs font-bold">
              {accountName}
            </span>
            <span className="mt-0.5 block truncate font-mono text-[10px] text-slate-600">
              {displayTotalAsset === undefined
                ? '资产读取中'
                : formatCompactCurrency(displayTotalAsset)}
            </span>
          </span>
          {isLoading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
        </button>
      </div>

      <StudioMenu
        ariaLabel="持仓菜单"
        items={[
          {
            icon: <BriefcaseBusiness size={14} />,
            id: 'select-holding',
            label: '切换到该持仓',
            onSelect: () => {
              if (menu?.payload) onHoldingSelect(menu.payload);
            },
          },
          {
            icon: <CandlestickChart size={14} />,
            id: 'open-stock-info',
            label: '查看股票信息',
            onSelect: () => {
              if (menu?.payload) onStockInfoOpen(menu.payload);
            },
          },
          ...(onHoldingOpenInNewWindow
            ? [
                {
                  icon: <ExternalLink size={14} />,
                  id: 'open-liquidation-new-tab',
                  label: '新窗口打开清仓',
                  onSelect: () => {
                    if (menu?.payload) onHoldingOpenInNewWindow(menu.payload);
                  },
                },
              ]
            : []),
        ]}
        menu={menu}
        onClose={closeMenu}
        width={180}
      />
    </aside>
  );
}
