import {
  Bot,
  CandlestickChart,
  Copy,
  FileText,
  LayoutGrid,
  Receipt,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';
import React from 'react';
import { useLocation } from 'wouter';

import { StudioMenu, useStudioMenu } from '@/components/studio-workbench';
import { useStudioNavigate } from '@/components/studio-workspace';
import { Button } from '@/components/ui/button';
import { logger } from '@/core/errors/logger';
import { SparklineChart } from '@/shared/components/charts/SparklineChart';
import {
  financialChartColor,
  financialToneBadgeClass,
  financialToneClass,
} from '@/shared/utils/financialColors';
import { cn } from '@/utils/cn';
import { formatCurrency, formatPercent } from '@/utils/transform/data';

import { useHoldingIntradayTrend } from '../hooks/useHoldingIntradayTrend';
import type { Position } from '../types';

interface HoldingCardProps {
  holding: Position;
  onLiquidate: (stockCode: string) => Promise<unknown>;
}

function copyText(value: string | number | undefined | null) {
  if (value === undefined || value === null) return;
  void navigator.clipboard?.writeText(String(value));
}

function getStockIconText(name: string): string {
  if (!name) return '?';
  if (name.length === 1) return name;
  return name.charAt(0) + name.charAt(name.length - 1);
}

function isInteractiveTarget(target: EventTarget | null) {
  return target instanceof HTMLElement
    ? Boolean(
        target.closest(
          'a, button, input, select, textarea, [role="button"], [data-studio-menu]'
        )
      )
    : false;
}

export function HoldingCard({ holding, onLiquidate }: HoldingCardProps) {
  const [, setLocation] = useLocation();
  const openStudioTab = useStudioNavigate();
  const { closeMenu, menu, openAtPointer } = useStudioMenu<Position>();

  const stockName = holding.instrumentName || holding.stockCode;
  const stockDetailPath = `/stock/${holding.stockCode}`;
  const isProfitable = (holding.profitLoss ?? 0) >= 0;
  const isTodayProfitable = (holding.todayProfitLoss ?? 0) >= 0;
  const isDayUp = (holding.changePercent ?? 0) >= 0;
  const { data: sparklineData, visibleRange } =
    useHoldingIntradayTrend(holding);

  const sparklineColor = financialChartColor(
    holding.changePercent ?? 0,
    'holding'
  );

  const handleLiquidate = async () => {
    try {
      if (window.confirm(`确定要清仓 ${stockName} 吗？`)) {
        await onLiquidate(holding.stockCode);
      }
    } catch (error) {
      logger.error('Liquidation failed:', { error });
    }
  };

  const handleRowClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!isInteractiveTarget(event.target)) setLocation(stockDetailPath);
  };

  const handleRowKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (
      (event.key === 'Enter' || event.key === ' ') &&
      !isInteractiveTarget(event.target)
    ) {
      event.preventDefault();
      setLocation(stockDetailPath);
    }
  };

  return (
    <>
      <div
        className="group grid min-h-[96px] cursor-pointer grid-cols-[minmax(240px,1.2fr)_minmax(180px,0.95fr)_minmax(160px,0.85fr)_minmax(220px,1fr)_112px] items-stretch overflow-hidden rounded-lg border border-white/5 bg-[#0b1120]/70 text-slate-200 transition-colors hover:border-red-500/25 hover:bg-white/[0.045] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/45"
        data-testid={`holding-card-${holding.stockCode}`}
        role="link"
        tabIndex={0}
        aria-label={`查看 ${stockName} 详情`}
        onClick={handleRowClick}
        onKeyDown={handleRowKeyDown}
        onContextMenu={event => openAtPointer(event, holding)}
      >
        <div className="flex min-w-0 items-center gap-3 border-r border-white/5 px-3 py-2">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-red-500/15 bg-red-500/10 text-xs font-black text-red-300">
            {getStockIconText(stockName)}
          </div>
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <h3 className="truncate text-sm font-black tracking-tight text-slate-100">
                {stockName}
              </h3>
              <span className="shrink-0 rounded border border-white/10 bg-white/[0.04] px-1.5 py-0.5 font-mono text-[10px] font-black text-slate-500">
                {holding.stockCode}
              </span>
            </div>
            <div className="mt-1 flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.16em] text-slate-600">
              <span>Realtime</span>
              <span className="h-1 w-1 rounded-full bg-emerald-400" />
              <span>Sync 1s</span>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-2 border-r border-white/5">
          <MetricCell
            label="现价"
            value={formatCurrency(holding.lastPrice ?? 0)}
          />
          <MetricCell label="数量" value={holding.volume.toLocaleString()} />
          <MetricCell
            label="成本"
            value={formatCurrency(holding.avgPrice ?? 0)}
          />
          <MetricCell
            label="仓位"
            value={`${(holding.marketValuePercent ?? 0).toFixed(1)}%`}
          />
        </div>

        <div className="grid grid-rows-2 border-r border-white/5">
          <PnLCell
            amount={holding.todayProfitLoss ?? 0}
            label="今日盈亏"
            value={`${isTodayProfitable ? '+' : ''}${formatCurrency(
              holding.todayProfitLoss ?? 0
            )}`}
          />
          <PnLCell
            amount={holding.profitLoss ?? 0}
            label="累计收益"
            value={`${isProfitable ? '+' : ''}${formatCurrency(
              holding.profitLoss ?? 0
            )}`}
            suffix={formatPercent(holding.profitRate ?? 0)}
          />
        </div>

        <div className="relative min-h-[96px] border-r border-white/5 px-3 py-2">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-600">
              Intraday
            </span>
            <span
              className={cn(
                'inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[10px] font-black',
                financialToneBadgeClass(
                  holding.changePercent ?? 0,
                  'holding'
                )
              )}
            >
              {isDayUp ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
              {isDayUp ? '+' : ''}
              {(holding.changePercent ?? 0).toFixed(2)}%
            </span>
          </div>
          <div className="absolute inset-x-3 bottom-2 top-7 opacity-70">
            <SparklineChart
              data={sparklineData}
              color={sparklineColor}
              visibleRange={visibleRange}
            />
          </div>
        </div>

        <div className="flex flex-col justify-center gap-1.5 px-2">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 justify-start gap-2 rounded-md px-2 text-[10px] font-black text-slate-300 hover:bg-white/10 hover:text-slate-50"
            onClick={event => {
              event.stopPropagation();
              openStudioTab(`/holdings?symbol=${holding.stockCode}`);
            }}
          >
            <LayoutGrid size={12} className="text-red-400" />
            快速交易
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 justify-start gap-2 rounded-md px-2 text-[10px] font-black text-rose-300 hover:bg-rose-500/10 hover:text-rose-200"
            onClick={event => {
              event.stopPropagation();
              void handleLiquidate();
            }}
          >
            <Receipt size={12} />
            清仓
          </Button>
        </div>
      </div>

      <StudioMenu
        ariaLabel="持仓行菜单"
        menu={menu}
        onClose={closeMenu}
        width={208}
        items={[
          {
            id: 'open-detail',
            label: '查看个股详情',
            icon: <CandlestickChart size={14} />,
            onSelect: () => setLocation(stockDetailPath),
          },
          {
            id: 'open-trading',
            label: '快速交易',
            icon: <LayoutGrid size={14} />,
            onSelect: () =>
              openStudioTab(`/holdings?symbol=${holding.stockCode}`),
          },
          {
            id: 'create-strategy',
            label: '创建策略',
            icon: <Bot size={14} />,
            onSelect: () =>
              setLocation(`/strategies/run?symbol=${holding.stockCode}`),
          },
          { id: 'sep-copy', type: 'separator' },
          {
            id: 'copy-code',
            label: '复制代码',
            icon: <Copy size={14} />,
            onSelect: () => copyText(holding.stockCode),
          },
          {
            id: 'copy-name',
            label: '复制名称',
            icon: <FileText size={14} />,
            onSelect: () => copyText(stockName),
          },
          { id: 'sep-danger', type: 'separator' },
          {
            id: 'liquidate',
            label: '清仓...',
            danger: true,
            icon: <Receipt size={14} />,
            onSelect: () => void handleLiquidate(),
          },
        ]}
      />
    </>
  );
}

function MetricCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 flex-col justify-center border-b border-r border-white/5 px-3 py-2 last:border-r-0 even:border-r-0 [&:nth-child(n+3)]:border-b-0">
      <span className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-600">
        {label}
      </span>
      <span className="mt-0.5 truncate font-mono text-xs font-black text-slate-200">
        {value}
      </span>
    </div>
  );
}

function PnLCell({
  amount,
  label,
  suffix,
  value,
}: {
  amount: number;
  label: string;
  suffix?: string;
  value: string;
}) {
  return (
    <div className="flex min-w-0 flex-col justify-center border-b border-white/5 px-3 py-2 last:border-b-0">
      <span className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-600">
        {label}
      </span>
      <div className="mt-0.5 flex min-w-0 items-baseline gap-2">
        <span
          className={cn(
            'truncate font-mono text-xs font-black',
            financialToneClass(amount, 'holding')
          )}
        >
          {value}
        </span>
        {suffix && (
          <span
            className={cn(
              'shrink-0 font-mono text-[10px] font-bold',
              financialToneClass(amount, 'holding'),
              'opacity-70'
            )}
          >
            {suffix}
          </span>
        )}
      </div>
    </div>
  );
}
