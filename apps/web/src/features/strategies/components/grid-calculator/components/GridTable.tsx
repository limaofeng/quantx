import { AlertTriangle } from 'lucide-react';
import React from 'react';

import { financialToneClass } from '@/shared/utils/financialColors';
import { cn } from '@/utils/cn';

import { type GridResult } from '../types';

interface Props {
  result: GridResult;
}

const GridTable: React.FC<Props> = ({ result }) => {
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const [isDragging, setIsDragging] = React.useState(false);
  const [startX, setStartX] = React.useState(0);
  const [startY, setStartY] = React.useState(0);
  const [scrollLeft, setScrollLeft] = React.useState(0);
  const [scrollTop, setScrollTop] = React.useState(0);

  if (result.levels.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-slate-500 dark:text-slate-400 p-ui-panel">
        <AlertTriangle className="w-8 h-8 mb-2 opacity-50 text-amber-500" />
        <p className="font-medium text-ui-label">无法生成有效网格</p>
        <p className="text-ui-caption opacity-75 mt-1">
          请检查预算或最小成交金额限制
        </p>
      </div>
    );
  }

  // Split into Sell (top) and Buy (bottom) for visual separation
  const displayLevels = [...result.levels].reverse();

  // Stats Calculation for visual breakdown
  const totalBuyPlanned = result.levels
    .filter(l => l.side === 'BUY')
    .reduce((acc, curr) => acc + curr.amount, 0);
  const totalInvested = result.guards.totalInvested;
  const currentPosValue = totalInvested - totalBuyPlanned;
  const maxCap = result.guards.maxPositionValue;

  const currentPosPct = Math.min(100, (currentPosValue / maxCap) * 100);
  const newGridPct = Math.min(100, (totalBuyPlanned / maxCap) * 100);

  const onMouseDown = (e: React.MouseEvent) => {
    if (!scrollRef.current) return;
    setIsDragging(true);
    setStartX(e.pageX - scrollRef.current.offsetLeft);
    setStartY(e.pageY - scrollRef.current.offsetTop);
    setScrollLeft(scrollRef.current.scrollLeft);
    setScrollTop(scrollRef.current.scrollTop);
  };

  const onMouseLeave = () => {
    setIsDragging(false);
  };

  const onMouseUp = () => {
    setIsDragging(false);
  };

  const onMouseMove = (e: React.MouseEvent) => {
    if (!isDragging || !scrollRef.current) return;
    e.preventDefault();
    const x = e.pageX - scrollRef.current.offsetLeft;
    const y = e.pageY - scrollRef.current.offsetTop;
    const walkX = (x - startX) * 1.5; // Drag speed multiplier
    const walkY = (y - startY) * 1.5;
    scrollRef.current.scrollLeft = scrollLeft - walkX;
    scrollRef.current.scrollTop = scrollTop - walkY;
  };

  return (
    <div className="flex flex-col h-full w-full">
      {/* Table Content */}
      <div
        ref={scrollRef}
        onMouseDown={onMouseDown}
        onMouseLeave={onMouseLeave}
        onMouseUp={onMouseUp}
        onMouseMove={onMouseMove}
        className={cn(
          'flex-1 overflow-auto custom-scrollbar select-none',
          isDragging ? 'cursor-grabbing' : 'cursor-grab'
        )}
      >
        <table className="w-full text-left border-collapse min-w-[600px]">
          <thead className="text-ui-caption text-muted-foreground uppercase bg-slate-100/50 dark:bg-slate-900/50 sticky top-0 z-10 backdrop-blur-sm">
            <tr>
              <th className="px-3 py-2 whitespace-nowrap font-bold w-[100px]">
                档位
              </th>
              <th className="px-3 py-2 whitespace-nowrap font-bold w-[100px]">
                价格
              </th>
              <th className="px-3 py-2 text-right whitespace-nowrap font-bold w-[100px]">
                股数
              </th>
              <th className="px-3 py-2 text-right whitespace-nowrap font-bold w-[100px]">
                金额 (¥)
              </th>
              <th className="px-3 py-2 text-right whitespace-nowrap font-bold w-[100px]">
                偏离
              </th>
              <th className="px-3 py-2 text-right whitespace-nowrap font-bold w-[100px]">
                预期收益
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800/50">
            {displayLevels.map(level => (
              <tr
                key={level.id}
                className={cn(
                  'hover:bg-slate-100/50 dark:hover:bg-slate-800/50 transition-colors group',
                  level.side === 'SELL'
                    ? 'bg-market-down/[0.02]'
                    : 'bg-market-up/[0.02]'
                )}
              >
                <td className="px-3 py-1.5 font-medium flex items-center gap-2">
                  <span
                    className={cn(
                      'px-1.5 py-0.5 rounded-sm text-ui-caption flex items-center w-10 justify-center font-bold tracking-tight',
                      level.side === 'SELL'
                        ? 'border border-market-down/20 bg-market-down/10 text-market-down'
                        : 'border border-market-up/20 bg-market-up/10 text-market-up'
                    )}
                  >
                    {level.side === 'SELL' ? '卖出' : '买入'}
                  </span>
                  <span className="text-muted-foreground/50 font-mono text-ui-caption">
                    #{Math.abs(level.levelIndex)}
                  </span>
                </td>
                <td className="px-3 py-1.5 font-mono font-bold text-ui-label text-foreground/80">
                  {level.price.toFixed(2)}
                </td>
                <td className="px-3 py-1.5 text-right font-mono text-ui-label text-muted-foreground">
                  {level.shares.toLocaleString()}
                </td>
                <td className="px-3 py-1.5 text-right font-mono text-ui-label text-muted-foreground">
                  {level.amount.toLocaleString()}
                </td>
                <td
                  className={cn(
                    'px-3 py-1.5 text-right font-mono text-ui-label',
                    financialToneClass(level.pctFromBase)
                  )}
                >
                  {level.pctFromBase > 0 ? '+' : ''}
                  {level.pctFromBase.toFixed(2)}%
                </td>
                <td className="px-3 py-1.5 text-right font-mono text-ui-label text-market-up font-bold opacity-60 group-hover:opacity-100">
                  +{level.expectedProfit.toFixed(1)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Footer Stats Breakdown */}
      <div className="border-t border-slate-200/50 dark:border-slate-800/50 p-3 shrink-0 bg-slate-50/30 dark:bg-black/20">
        <div className="flex gap-ui-section text-ui-caption mb-2">
          <div className="flex-1 space-y-1">
            <div className="flex justify-between text-muted-foreground">
              <span>持仓占用</span>
              <span className="font-mono text-foreground">
                ¥{currentPosValue.toLocaleString()}
              </span>
            </div>
            <div className="flex justify-between text-muted-foreground">
              <span>网格预算</span>
              <span className="font-mono text-foreground">
                ¥{totalBuyPlanned.toLocaleString()}
              </span>
            </div>
          </div>
          <div className="flex-1 space-y-1">
            <div className="flex justify-between text-muted-foreground">
              <span>总敞口</span>
              <span className="font-mono text-foreground">
                ¥{totalInvested.toLocaleString()}
              </span>
            </div>
            <div className="flex justify-between text-muted-foreground">
              <span>使用率</span>
              <span className="font-mono font-bold text-foreground">
                {Math.round((totalInvested / maxCap) * 100)}%
              </span>
            </div>
          </div>
        </div>

        {/* Visual Progress Bar */}
        <div className="relative w-full bg-slate-200/50 dark:bg-slate-800/50 rounded-full h-1 overflow-hidden flex">
          <div
            className="bg-blue-500/70 h-full"
            style={{ width: `${currentPosPct}%` }}
          ></div>
          <div
            className="bg-emerald-500/70 h-full"
            style={{ width: `${newGridPct}%` }}
          ></div>
        </div>
      </div>
    </div>
  );
};

export default GridTable;
