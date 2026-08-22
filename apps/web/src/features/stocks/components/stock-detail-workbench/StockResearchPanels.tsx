import {
  AlertTriangle,
  FileText,
  RefreshCw,
  ShieldCheck,
  WalletCards,
} from 'lucide-react';
import * as React from 'react';

import { TradingChart } from '@/components/trading-chart';
import { Button } from '@/components/ui/button';
import type {
  PortfolioSummaryData,
  Position,
} from '@/features/portfolio/types';
import { TradingCard } from '@/features/trading/components/TradingCard';
import type {
  StockDisclosureSummaryQuery,
  StockWorkspaceFinancialsQuery,
} from '@/generated/gql/graphql';
import type { Stock } from '@/shared/types';
import { cn } from '@/utils/cn';

import { DisclosureTimeline } from './DisclosureTimeline';
import { formatDate, formatPercent, formatPrice } from './formatters';
import { RepurchaseBrief } from './RepurchaseBrief';

type DisclosureSummary = NonNullable<
  StockDisclosureSummaryQuery['stockDisclosureSummary']
>;
type FinancialSummary = NonNullable<
  StockWorkspaceFinancialsQuery['financialSummary']
>;

interface TradingPanelProps {
  holdings: Position[];
  onStockSelect?: (stock: Stock | null) => void;
  portfolioSummary?: PortfolioSummaryData;
  priceUpdate?: { price: string; timestamp: number } | null;
  stockCode: string;
}

function toFiniteNumber(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatCompactCurrency(value: unknown, signed = false) {
  const number = toFiniteNumber(value);
  if (number === null) return '--';
  const absolute = Math.abs(number);
  const prefix = number < 0 ? '-' : signed && number > 0 ? '+' : '';
  if (absolute >= 1e8) return `${prefix}¥${(absolute / 1e8).toFixed(2)}亿`;
  if (absolute >= 1e4) return `${prefix}¥${(absolute / 1e4).toFixed(2)}万`;
  return `${prefix}¥${absolute.toFixed(2)}`;
}

function formatFinancialMetric(value: unknown) {
  const number = toFiniteNumber(value);
  if (number === null) return '--';
  if (Math.abs(number) >= 1e8) return `${(number / 1e8).toFixed(2)}亿`;
  if (Math.abs(number) >= 1e4) return `${(number / 1e4).toFixed(2)}万`;
  return number.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}

function reportPeriod(value?: string | null) {
  if (!value) return '--';
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.getFullYear()}Q${Math.ceil((date.getMonth() + 1) / 3)}`;
}

function WorkspacePanel({
  children,
  className,
  icon: Icon,
  title,
}: {
  children: React.ReactNode;
  className?: string;
  icon: React.ElementType;
  title: string;
}) {
  return (
    <section
      className={cn('min-w-0 border border-white/5 bg-[#0b1120]/75', className)}
    >
      <div className="flex h-9 items-center gap-2 border-b border-white/5 px-3">
        <Icon className="h-3.5 w-3.5 text-red-300" />
        <h3 className="truncate text-[11px] font-black text-slate-200">
          {title}
        </h3>
      </div>
      {children}
    </section>
  );
}

export function StockHoldingSnapshot({
  holding,
}: {
  holding?: Position | null;
}) {
  const metrics = [
    {
      label: '持仓',
      value: holding
        ? `${Number(holding.volume || 0).toLocaleString()} 股`
        : '--',
    },
    {
      label: '可用',
      value: holding
        ? `${Number(holding.canUseVolume || 0).toLocaleString()} 股`
        : '--',
    },
    { label: '成本', value: formatPrice(holding?.avgPrice) },
    {
      label: '浮动盈亏',
      tone: holding?.profitLoss,
      value: formatCompactCurrency(holding?.profitLoss, true),
    },
  ];

  return (
    <WorkspacePanel icon={WalletCards} title="持仓快照">
      <div className="grid grid-cols-2 gap-px bg-white/5 xl:grid-cols-4 2xl:grid-cols-2">
        {metrics.map(metric => (
          <div key={metric.label} className="min-w-0 bg-[#0b1120] px-3 py-2.5">
            <div className="text-[10px] font-bold text-slate-500">
              {metric.label}
            </div>
            <div
              className={cn(
                'mt-1 truncate font-mono text-xs font-black tabular-nums text-slate-200',
                metric.tone != null &&
                  Number(metric.tone) < 0 &&
                  'text-holding-down',
                metric.tone != null &&
                  Number(metric.tone) > 0 &&
                  'text-market-up'
              )}
            >
              {metric.value}
            </div>
          </div>
        ))}
      </div>
      {!holding && (
        <div className="border-t border-white/5 px-3 py-2 text-[10px] font-bold text-amber-200">
          当前标的未持仓，平仓操作不可用。
        </div>
      )}
    </WorkspacePanel>
  );
}

function AnnouncementPreview({
  disclosure,
  isLoading,
}: {
  disclosure?: DisclosureSummary | null;
  isLoading: boolean;
}) {
  const announcements = disclosure?.announcements.slice(0, 3) ?? [];
  return (
    <WorkspacePanel icon={FileText} title="最新公告" className="h-full">
      <div className="h-[calc(100%-2.25rem)] overflow-y-auto p-2 custom-scrollbar">
        {isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, index) => (
              <div key={index} className="h-10 skeleton-shimmer" />
            ))}
          </div>
        ) : announcements.length === 0 ? (
          <div className="flex h-full min-h-24 flex-col items-center justify-center text-center">
            <div className="text-xs font-bold text-slate-400">暂无公告</div>
            <div className="mt-1 text-[10px] text-slate-600">
              {disclosure?.sourceMessage || '等待公告数据同步'}
            </div>
          </div>
        ) : (
          <div className="space-y-1.5">
            {announcements.map(item => (
              <a
                key={item.id}
                href={item.sourceUrl || item.pdfUrl || undefined}
                target={item.sourceUrl || item.pdfUrl ? '_blank' : undefined}
                rel={item.sourceUrl || item.pdfUrl ? 'noreferrer' : undefined}
                aria-disabled={!item.sourceUrl && !item.pdfUrl}
                className={cn(
                  'grid min-w-0 grid-cols-[76px_minmax(0,1fr)] items-center gap-2 border border-white/5 bg-[#08101d]/70 px-2.5 py-2 transition-colors',
                  item.sourceUrl || item.pdfUrl
                    ? 'hover:border-red-500/25 hover:bg-red-500/[0.04]'
                    : 'cursor-default'
                )}
              >
                <span className="font-mono text-[10px] font-bold text-slate-600">
                  {formatDate(item.announceDate)}
                </span>
                <span className="truncate text-[11px] font-bold text-slate-300">
                  {item.title}
                </span>
              </a>
            ))}
          </div>
        )}
      </div>
    </WorkspacePanel>
  );
}

function FinancialPreview({
  isLoading,
  summary,
}: {
  isLoading: boolean;
  summary?: FinancialSummary | null;
}) {
  const totalAssets = toFiniteNumber(summary?.totalAssets);
  const totalLiabilities = toFiniteNumber(summary?.totalLiabilities);
  const metrics = [
    { label: '营业收入', value: formatFinancialMetric(summary?.revenue) },
    {
      label: '归母净利润',
      value: formatFinancialMetric(summary?.netProfitExclMinIntInc),
    },
    { label: 'EPS', value: formatFinancialMetric(summary?.epsBasic) },
    {
      label: '资产负债率',
      value:
        totalAssets !== null && totalAssets !== 0 && totalLiabilities !== null
          ? formatPercent((totalLiabilities / totalAssets) * 100, false)
          : '--',
    },
  ];

  return (
    <WorkspacePanel icon={ShieldCheck} title="财务快照" className="h-full">
      {isLoading && !summary ? (
        <div className="m-2 h-24 skeleton-shimmer" />
      ) : (
        <div className="p-2">
          <div className="mb-2 flex items-center justify-between gap-3 px-1">
            <span className="text-[10px] font-bold text-slate-600">报告期</span>
            <span className="font-mono text-[10px] font-black text-slate-300">
              {reportPeriod(summary?.latestReportDate)}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            {metrics.map(metric => (
              <div
                key={metric.label}
                className="min-w-0 border border-white/5 bg-[#08101d]/70 px-2.5 py-2"
              >
                <div className="truncate text-[9px] font-bold text-slate-600">
                  {metric.label}
                </div>
                <div className="mt-1 truncate font-mono text-xs font-black text-slate-200">
                  {metric.value}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </WorkspacePanel>
  );
}

function OrderTicket({
  holdings,
  onStockSelect,
  portfolioSummary,
  priceUpdate,
  stockCode,
}: TradingPanelProps) {
  return (
    <div className="min-h-[440px] flex-1 overflow-hidden border border-white/5 bg-[#0b1120]/75">
      <TradingCard
        holdings={holdings}
        initialStockCode={stockCode}
        onStockSelect={onStockSelect}
        portfolioSummary={portfolioSummary}
        priceUpdate={priceUpdate}
      />
    </div>
  );
}

export function StockOverviewPanel({
  disclosure,
  disclosureLoading,
  financialLoading,
  financialSummary,
  holding,
  holdings,
  onStockSelect,
  portfolioSummary,
  stockCode,
}: TradingPanelProps & {
  disclosure?: DisclosureSummary | null;
  disclosureLoading: boolean;
  financialLoading: boolean;
  financialSummary?: FinancialSummary | null;
  holding?: Position | null;
}) {
  return (
    <div className="grid h-full min-h-[720px] grid-cols-1 gap-2 overflow-y-auto bg-[#08101d] p-2 custom-scrollbar xl:grid-cols-[minmax(0,1fr)_340px] xl:grid-rows-[minmax(470px,1fr)_180px]">
      <section className="min-h-[470px] min-w-0 overflow-hidden border border-white/5 bg-[#0b1120]/65">
        <TradingChart stockCode={stockCode} />
      </section>
      <aside className="flex min-h-[470px] min-w-0 flex-col gap-2">
        <StockHoldingSnapshot holding={holding} />
        <OrderTicket
          holdings={holdings}
          onStockSelect={onStockSelect}
          portfolioSummary={portfolioSummary}
          stockCode={stockCode}
        />
      </aside>
      <AnnouncementPreview
        disclosure={disclosure}
        isLoading={disclosureLoading}
      />
      <FinancialPreview
        isLoading={financialLoading}
        summary={financialSummary}
      />
    </div>
  );
}

export function StockFinancialRail({
  financialLoading,
  financialSummary,
  holding,
  holdings,
  onStockSelect,
  portfolioSummary,
  stockCode,
}: TradingPanelProps & {
  financialLoading: boolean;
  financialSummary?: FinancialSummary | null;
  holding?: Position | null;
}) {
  const completeness = financialSummary
    ? [
        { label: '利润表', count: financialSummary.incomeCount },
        { label: '资产负债表', count: financialSummary.balanceCount },
        { label: '现金流量表', count: financialSummary.cashFlowCount },
        { label: '股本结构', count: financialSummary.capitalCount },
      ]
    : [];

  return (
    <aside className="flex min-h-0 flex-col gap-2 overflow-y-auto custom-scrollbar">
      <StockHoldingSnapshot holding={holding} />
      <WorkspacePanel icon={ShieldCheck} title="数据质量">
        {financialLoading && !financialSummary ? (
          <div className="m-2 h-24 skeleton-shimmer" />
        ) : (
          <div className="space-y-2 p-3">
            <div className="flex items-center justify-between text-[10px] font-bold">
              <span className="text-slate-500">最新报告</span>
              <span className="font-mono text-slate-300">
                {reportPeriod(financialSummary?.latestReportDate)}
              </span>
            </div>
            {completeness.map(item => (
              <div
                key={item.label}
                className="flex items-center justify-between text-[10px] font-bold"
              >
                <span className="text-slate-500">{item.label}</span>
                <span
                  className={
                    item.count > 0 ? 'text-emerald-300' : 'text-amber-300'
                  }
                >
                  {item.count > 0 ? `${item.count} 期` : '缺失'}
                </span>
              </div>
            ))}
          </div>
        )}
      </WorkspacePanel>
      <OrderTicket
        holdings={holdings}
        onStockSelect={onStockSelect}
        portfolioSummary={portfolioSummary}
        stockCode={stockCode}
      />
    </aside>
  );
}

export function StockAnnouncementsPanel({
  disclosure,
  error,
  isLoading,
  isRefreshing,
  onRefresh,
}: {
  disclosure?: DisclosureSummary | null;
  error?: Error | null;
  isLoading: boolean;
  isRefreshing: boolean;
  onRefresh: () => void;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto bg-[#08101d] p-2 custom-scrollbar">
      <div className="flex flex-wrap items-center justify-between gap-2 border border-white/5 bg-[#0b1120]/80 px-3 py-2">
        <div>
          <div className="text-[10px] font-black uppercase tracking-[0.18em] text-red-300">
            Disclosures
          </div>
          <div className="mt-1 flex items-center gap-2">
            <h2 className="text-sm font-black text-slate-100">
              公告与公司行动
            </h2>
            <span className="font-mono text-[10px] font-bold text-slate-600">
              {disclosure?.announcements.length ?? 0} 条
            </span>
          </div>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 border-white/10 bg-white/[0.03] text-xs text-slate-300"
          disabled={isRefreshing}
          onClick={onRefresh}
        >
          <RefreshCw
            className={cn('mr-2 h-3.5 w-3.5', isRefreshing && 'animate-spin')}
          />
          刷新公告
        </Button>
      </div>
      {error && (
        <div
          role="alert"
          className="flex items-center gap-2 border border-amber-400/20 bg-amber-500/10 px-3 py-2 text-[11px] font-bold text-amber-200"
        >
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          {error.message}
        </div>
      )}
      <div className="grid min-h-[500px] gap-2 xl:grid-cols-[minmax(0,1.45fr)_minmax(340px,0.55fr)]">
        <DisclosureTimeline
          announcements={disclosure?.announcements ?? []}
          isLoading={isLoading}
          sourceStatus={disclosure?.sourceStatus}
        />
        <RepurchaseBrief
          event={disclosure?.repurchaseEvents[0] ?? null}
          sourceStatus={disclosure?.sourceStatus}
        />
      </div>
    </div>
  );
}

export function StockSummaryStrip({
  disclosure,
  financialSummary,
  holding,
  lastPrice,
}: {
  disclosure?: DisclosureSummary | null;
  financialSummary?: FinancialSummary | null;
  holding?: Position | null;
  lastPrice?: unknown;
}) {
  const metrics = [
    { label: '最新价', value: formatPrice(lastPrice) },
    { label: '持仓市值', value: formatCompactCurrency(holding?.marketValue) },
    {
      label: '浮动盈亏',
      tone: holding?.profitLoss,
      value: formatCompactCurrency(holding?.profitLoss, true),
    },
  ];
  const announcements = disclosure?.announcements.slice(0, 2) ?? [];

  return (
    <section className="min-h-0 border border-white/5 bg-[#0b1120]/80">
      <div className="flex h-9 items-center justify-between border-b border-white/5 px-3">
        <h2 className="text-[11px] font-black text-slate-200">个股摘要</h2>
        <span className="font-mono text-[9px] font-bold text-slate-600">
          {reportPeriod(financialSummary?.latestReportDate)}
        </span>
      </div>
      <div className="grid min-h-[118px] grid-cols-2 gap-px bg-white/5 md:grid-cols-4 xl:grid-cols-[repeat(3,minmax(112px,0.55fr))_minmax(260px,1.45fr)_minmax(250px,1.2fr)]">
        {metrics.map(metric => (
          <div key={metric.label} className="min-w-0 bg-[#0b1120] px-3 py-3">
            <div className="text-[9px] font-bold text-slate-600">
              {metric.label}
            </div>
            <div
              className={cn(
                'mt-2 truncate font-mono text-sm font-black text-slate-100',
                metric.tone != null &&
                  Number(metric.tone) < 0 &&
                  'text-holding-down',
                metric.tone != null &&
                  Number(metric.tone) > 0 &&
                  'text-market-up'
              )}
            >
              {metric.value}
            </div>
          </div>
        ))}
        <div className="min-w-0 bg-[#0b1120] px-3 py-3">
          <div className="text-[9px] font-bold text-slate-600">最新公告</div>
          <div className="mt-2 space-y-1.5">
            {announcements.length > 0 ? (
              announcements.map(item => (
                <div key={item.id} className="flex min-w-0 items-center gap-2">
                  <span className="shrink-0 font-mono text-[9px] text-slate-600">
                    {formatDate(item.announceDate)}
                  </span>
                  <span className="truncate text-[10px] font-bold text-slate-300">
                    {item.title}
                  </span>
                </div>
              ))
            ) : (
              <span className="text-[10px] font-bold text-slate-600">
                暂无公告
              </span>
            )}
          </div>
        </div>
        <div className="min-w-0 bg-[#0b1120] px-3 py-3">
          <div className="text-[9px] font-bold text-slate-600">财务快照</div>
          <div className="mt-2 grid grid-cols-3 gap-2">
            {[
              { label: '营收', value: financialSummary?.revenue },
              {
                label: '归母净利',
                value: financialSummary?.netProfitExclMinIntInc,
              },
              { label: 'EPS', value: financialSummary?.epsBasic },
            ].map(item => (
              <div key={item.label} className="min-w-0">
                <div className="truncate text-[9px] text-slate-600">
                  {item.label}
                </div>
                <div className="mt-1 truncate font-mono text-[10px] font-black text-slate-300">
                  {formatFinancialMetric(item.value)}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
