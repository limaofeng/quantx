import {
  TrendingDown,
  TrendingUp,
  MoreHorizontal,
  LayoutGrid,
  Receipt,
  Globe,
  Activity,
} from 'lucide-react';
import React from 'react';
import { Link, useLocation } from 'wouter';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { logger } from '@/core/errors/logger';
import { SparklineChart } from '@/shared/components/charts/SparklineChart';
import { formatCurrency, formatPercent } from '@/utils/transform/data';

import { useHoldingIntradayTrend } from '../hooks/useHoldingIntradayTrend';
import { useHoldings } from '../hooks/useHoldings';
import type { Position } from '../types';

interface HoldingCardProps {
  holding: Position;
}

// 获取股票图标文字
function getStockIconText(_name: string): string {
  if (!_name || _name.length === 0) return '?';
  if (_name.length === 1) return _name;
  return _name.charAt(0) + _name.charAt(_name.length - 1);
}

export function HoldingCard({ holding }: HoldingCardProps) {
  const [, setLocation] = useLocation();
  const { liquidateHolding } = useHoldings();
  const isProfitable = (holding.profitLoss ?? 0) >= 0;
  const isTodayProfitable = (holding.todayProfitLoss ?? 0) >= 0;
  const isDayUp = (holding.changePercent ?? 0) >= 0;
  const { data: sparklineData, visibleRange } =
    useHoldingIntradayTrend(holding);

  const sparklineColor = isDayUp ? '#22c55e' : '#ef4444';

  const handleLiquidate = async () => {
    try {
      if (
        window.confirm(
          `确定要清仓 ${holding.instrumentName || holding.stockCode} 吗？`
        )
      ) {
        await liquidateHolding(holding.id);
      }
    } catch (error) {
      logger.error('Liquidation failed:', { error });
    }
  };

  const stockDetailPath = `/stock/${holding.stockCode}`;

  const isInteractiveTarget = (target: EventTarget | null) => {
    return target instanceof HTMLElement
      ? Boolean(
          target.closest(
            'a, button, input, select, textarea, [role="button"], [data-radix-popper-content-wrapper]'
          )
        )
      : false;
  };

  const handleCardClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!isInteractiveTarget(event.target)) {
      setLocation(stockDetailPath);
    }
  };

  const handleCardKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (
      (event.key === 'Enter' || event.key === ' ') &&
      !isInteractiveTarget(event.target)
    ) {
      event.preventDefault();
      setLocation(stockDetailPath);
    }
  };

  return (
    <div className="group relative">
      <Card
        className="relative overflow-hidden border border-slate-200 dark:border-white/5 bg-white dark:bg-slate-900/40 backdrop-blur-xl hover:border-primary/30 hover:shadow-[0_20px_50px_rgba(59,130,246,0.12)] transition-all duration-500 cursor-pointer rounded-[2rem]"
        data-testid={`holding-card-${holding.stockCode}`}
        role="link"
        tabIndex={0}
        aria-label={`查看 ${holding.instrumentName || holding.stockCode} 详情`}
        onClick={handleCardClick}
        onKeyDown={handleCardKeyDown}
      >
        <div className="flex flex-col md:flex-row items-stretch overflow-hidden">
          {/* Left section: Identity & Primary Metric */}
          <div className="relative z-10 flex-[1] flex flex-col min-w-0">
            {/* Background Sparkline - Localized on the right with bottom clearance and multi-directional fade */}
            <div
              className="absolute right-0 top-1 bottom-6 z-0 opacity-25 pointer-events-none overflow-hidden w-[60%]"
              style={{
                maskImage:
                  'linear-gradient(to right, transparent, black 15%), linear-gradient(to bottom, black 70%, transparent)',
                WebkitMaskImage:
                  'linear-gradient(to right, transparent, black 15%), linear-gradient(to bottom, black 70%, transparent)',
                maskComposite: 'intersect',
                WebkitMaskComposite: 'source-in',
              }}
            >
              <div className="absolute inset-0 -bottom-2 -left-2 -right-2">
                <SparklineChart
                  data={sparklineData}
                  color={sparklineColor}
                  visibleRange={visibleRange}
                />
              </div>
            </div>

            <div className="relative z-10 p-4 pb-3 flex-1 flex flex-col justify-between space-y-3">
              <div className="flex justify-between items-start">
                <Link
                  href={stockDetailPath}
                  className="flex gap-4 min-w-0 group/info"
                >
                  <div className="shrink-0">
                    <div className="w-14 h-14 rounded-2xl flex items-center justify-center bg-primary/5 border border-primary/10 transition-all duration-500 group-hover/info:rotate-6">
                      <span className="text-primary text-base font-bold">
                        {getStockIconText(
                          holding.instrumentName || holding.stockCode
                        )}
                      </span>
                    </div>
                  </div>

                  <div className="min-w-0 flex flex-col pt-0.5">
                    <div className="flex items-center gap-2 mb-0.5">
                      <h3 className="text-lg font-black text-slate-900 dark:text-white tracking-tight truncate">
                        {holding.instrumentName || holding.stockCode}
                      </h3>
                      <span className="px-1.5 py-0.5 rounded-md text-[9px] font-black uppercase tracking-wider bg-slate-100 dark:bg-white/5 text-slate-500 border border-slate-200 dark:border-white/5 font-mono">
                        {holding.stockCode}
                      </span>
                    </div>
                    <div className="flex items-baseline gap-2">
                      <span
                        className="text-2xl font-black tracking-tighter"
                        data-testid={`holding-${holding.stockCode}-price`}
                      >
                        {formatCurrency(holding.lastPrice ?? 0)}
                      </span>
                      {'changePercent' in holding && (
                        <span
                          className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                            isDayUp
                              ? 'bg-success/10 text-success'
                              : 'bg-destructive/10 text-destructive'
                          }`}
                        >
                          {isDayUp ? '+' : ''}
                          {(holding.changePercent ?? 0).toFixed(2)}%
                        </span>
                      )}
                    </div>

                    {/* Position Weight moved here */}
                    <div className="flex items-center gap-2 max-w-[120px] mt-2">
                      <div className="h-1 bg-slate-100 dark:bg-white/10 rounded-full overflow-hidden flex-1">
                        <div
                          className="h-full bg-primary/60 transition-all duration-1000"
                          style={{
                            width: `${Math.min((holding.marketValuePercent ?? 0) * 10, 100)}%`,
                          }}
                        />
                      </div>
                      <span className="text-[8px] font-black text-slate-400 font-mono">
                        {(holding.marketValuePercent ?? 0).toFixed(1)}%
                      </span>
                    </div>
                  </div>
                </Link>

                <div className="md:hidden">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" className="h-8 w-8">
                        <MoreHorizontal className="h-4 w-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem asChild>
                        <Link href={`/trading?symbol=${holding.stockCode}`}>
                          买入 / 卖出
                        </Link>
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        className="text-destructive"
                        onClick={handleLiquidate}
                      >
                        清仓
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </div>
            </div>

            {/* Bottom Info Bar */}
            <div className="h-10 bg-slate-50/50 dark:bg-black/20 px-5 flex items-center gap-4 border-t border-slate-100 dark:border-white/5 mt-auto">
              <div className="flex items-center gap-1.5 text-slate-400">
                <Globe size={12} />
                <span className="text-[10px] font-bold font-mono uppercase tracking-tight opacity-70">
                  REALTIME
                </span>
              </div>
              <div className="w-px h-3 bg-slate-200 dark:bg-white/10" />
              <div className="flex items-center gap-1.5 text-slate-400">
                <Activity size={12} />
                <span className="text-[10px] font-bold font-mono opacity-70">
                  SYC: 1s
                </span>
              </div>
            </div>
          </div>

          {/* Right Section: Slanted Metrics with Hover Expansion */}
          <div
            className="relative z-20 flex-[1.4] transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] group-hover:md:-translate-x-[160px] hidden md:block"
            style={{
              marginRight: '-160px',
              marginLeft: '-40px',
            }}
          >
            <div
              className="absolute inset-0 bg-white dark:bg-[#0f172a] shadow-[-10px_0_15px_rgba(0,0,0,0.05)] h-full overflow-hidden"
              style={{ clipPath: 'polygon(40px 0, 100% 0, 100% 100%, 0 100%)' }}
            >
              <div className="flex h-full">
                {/* Main Metrics Area */}
                {/* Main Metrics Area */}
                <div className="flex-1 flex flex-col min-h-0">
                  {/* Dense Stats Header */}
                  <div className="flex border-b border-slate-100 dark:border-white/5 bg-slate-50/50 dark:bg-black/20">
                    <div className="flex-1 py-2.5 pl-14 border-r border-slate-100 dark:border-white/5">
                      <span className="text-[7px] font-black text-slate-400 uppercase tracking-widest block mb-0.5">
                        持仓数量
                      </span>
                      <span className="text-[11px] font-bold text-slate-900 dark:text-slate-200 font-mono tracking-tight">
                        {holding.volume}
                      </span>
                    </div>
                    <div className="flex-1 py-2.5 px-3.5 border-r border-slate-100 dark:border-white/5">
                      <span className="text-[7px] font-black text-slate-400 uppercase tracking-widest block mb-0.5">
                        成本价
                      </span>
                      <span className="text-[11px] font-bold text-slate-900 dark:text-slate-200 font-mono tracking-tight">
                        {formatCurrency(holding.avgPrice ?? 0)}
                      </span>
                    </div>
                    <div className="flex-1 py-2.5 px-3.5">
                      <span className="text-[7px] font-black text-slate-400 uppercase tracking-widest block mb-0.5">
                        今日盈亏
                      </span>
                      <span
                        className={`text-[11px] font-bold font-mono tracking-tight ${isTodayProfitable ? 'text-success' : 'text-destructive'}`}
                      >
                        {isTodayProfitable ? '+' : ''}
                        {formatCurrency(holding.todayProfitLoss ?? 0)}
                      </span>
                    </div>
                  </div>

                  {/* Hero Metrics Area */}
                  <div
                    className={`grid grid-cols-2 ${isProfitable ? 'bg-success/5' : 'bg-destructive/5'}`}
                  >
                    <div className="p-3 pl-14 flex flex-col justify-center border-r border-slate-100 dark:border-white/5">
                      <span className="text-[7px] font-black text-slate-400 uppercase tracking-widest mb-0.5">
                        当前市值
                      </span>
                      <div className="text-lg font-black text-slate-900 dark:text-slate-200 font-mono tracking-tighter leading-none">
                        {formatCurrency(holding.marketValue ?? 0)}
                      </div>
                    </div>
                    <div className="p-3 flex flex-col justify-center">
                      <div className="flex items-center justify-between mb-0.5">
                        <span className="text-[7px] font-black text-slate-400 uppercase tracking-widest">
                          累计收益
                        </span>
                        {isProfitable ? (
                          <TrendingUp size={10} className="text-success" />
                        ) : (
                          <TrendingDown
                            size={10}
                            className="text-destructive"
                          />
                        )}
                      </div>
                      <div className="flex items-baseline justify-between leading-none">
                        <div
                          className={`text-lg font-black tracking-tighter ${isProfitable ? 'text-success' : 'text-destructive'}`}
                        >
                          {isProfitable ? '+' : ''}
                          {formatCurrency(holding.profitLoss ?? 0)}
                        </div>
                        <div
                          className={`text-[9px] font-bold ${isProfitable ? 'text-success/80' : 'text-destructive/80'}`}
                        >
                          {formatPercent(holding.profitRate ?? 0)}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Expansion Panel (Hidden until hover) */}
                <div className="w-[160px] h-full bg-slate-950 px-4 py-4 border-l border-white/5 flex flex-col gap-4 shrink-0">
                  <div className="space-y-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="w-full justify-start gap-2 h-8 rounded-lg text-[10px] font-black text-white/80 hover:text-white hover:bg-white/10 px-2.5 border border-white/5"
                      asChild
                    >
                      <Link href={`/trading?symbol=${holding.stockCode}`}>
                        <LayoutGrid size={12} className="text-primary" />
                        快速交易
                      </Link>
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="w-full justify-start gap-2 h-8 rounded-lg text-[10px] font-black text-rose-400 hover:text-rose-300 hover:bg-rose-500/10 px-2.5 border border-rose-500/10"
                      onClick={handleLiquidate}
                    >
                      <Receipt size={12} />
                      一键清仓
                    </Button>
                  </div>

                  <div className="space-y-1.5 mt-auto">
                    <p className="text-[7px] font-black text-white/40 uppercase tracking-widest">
                      止盈止损参考
                    </p>
                    <div className="flex flex-wrap gap-1">
                      <span className="px-1.5 py-0.5 rounded-[4px] text-[7px] font-black bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                        TP: +15%
                      </span>
                      <span className="px-1.5 py-0.5 rounded-[4px] text-[7px] font-black bg-rose-500/10 text-rose-400 border border-rose-500/20">
                        SL: -5%
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Mobile quick actions (visible only on mobile) */}
        <div className="md:hidden flex p-3 gap-2 border-t border-slate-100 dark:border-white/5 bg-slate-50/50 dark:bg-black/10">
          <Button
            variant="outline"
            size="sm"
            className="flex-1 h-9 rounded-xl text-xs font-bold"
            asChild
          >
            <Link href={`/trading?symbol=${holding.stockCode}`}>交易</Link>
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="flex-1 h-9 rounded-xl text-xs font-bold text-destructive hover:bg-destructive/10"
            onClick={handleLiquidate}
          >
            清仓
          </Button>
        </div>
      </Card>

      {/* Decorative Outer Glow */}
      <div className="absolute -inset-2 bg-gradient-to-r from-primary/0 via-primary/5 to-primary/0 rounded-[2.5rem] opacity-0 group-hover:opacity-100 blur-2xl transition-opacity duration-700 -z-10" />
    </div>
  );
}
