import { BarChart2, Timer } from 'lucide-react';

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/utils/cn';

import type { StrategyChartPeriod } from './types';
import {
  BACKTEST_COMMON_PERIODS,
  BACKTEST_MINUTE_PERIODS,
  BACKTEST_PERIODS,
} from './utils';

interface StrategyChartControlsProps {
  activePeriod: StrategyChartPeriod;
  onPeriodChange: (period: StrategyChartPeriod) => void;
}

export function StrategyChartControls({
  activePeriod,
  onPeriodChange,
}: StrategyChartControlsProps) {
  const isTickPeriod = activePeriod === 'TICK';
  const activeKLineLabel =
    BACKTEST_PERIODS.find(period => period.value === activePeriod)?.label ||
    '日K';

  return (
    <div className="absolute top-4 left-4 z-20 flex items-center gap-2">
      <div className="h-9 p-1 rounded-xl bg-slate-950/85 backdrop-blur-md border border-white/10 flex items-center shadow-lg shadow-black/20">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              className={cn(
                'px-3 h-full rounded-lg text-[11px] font-bold transition-all duration-300 flex items-center gap-1.5',
                !isTickPeriod
                  ? 'bg-blue-600 text-white shadow-sm'
                  : 'text-slate-400 hover:bg-white/10 hover:text-white'
              )}
              onClick={() => {
                if (isTickPeriod) onPeriodChange('DAY_1');
              }}
            >
              <BarChart2 className="w-3.5 h-3.5" />
              {activeKLineLabel}
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="start"
            className="w-[140px] max-h-[320px] overflow-y-auto custom-scrollbar border-white/10 bg-[#050A14] text-slate-200"
          >
            <div className="px-2 py-1.5 text-[10px] uppercase font-bold text-slate-500">
              常用周期
            </div>
            {BACKTEST_COMMON_PERIODS.map(period => (
              <DropdownMenuItem
                key={period.value}
                className={cn(
                  'text-xs cursor-pointer',
                  activePeriod === period.value &&
                    'text-blue-400 bg-blue-600/10'
                )}
                onClick={() => onPeriodChange(period.value)}
              >
                {period.label}
              </DropdownMenuItem>
            ))}
            <div className="px-2 py-1.5 text-[10px] uppercase font-bold text-slate-500 mt-2 border-t border-white/10 pt-2">
              分钟周期
            </div>
            {BACKTEST_MINUTE_PERIODS.map(period => (
              <DropdownMenuItem
                key={period.value}
                className={cn(
                  'text-xs cursor-pointer',
                  activePeriod === period.value &&
                    'text-blue-400 bg-blue-600/10'
                )}
                onClick={() => onPeriodChange(period.value)}
              >
                {period.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        <div className="w-px h-4 bg-slate-700/70 mx-1" />

        <button
          className={cn(
            'px-3 h-full rounded-lg text-[11px] font-bold transition-all duration-300 flex items-center gap-1.5',
            isTickPeriod
              ? 'bg-blue-600 text-white shadow-sm'
              : 'text-slate-400 hover:bg-white/10 hover:text-white'
          )}
          onClick={() => onPeriodChange('TICK')}
        >
          <Timer className="w-3.5 h-3.5" />
          Tick
        </button>
      </div>
    </div>
  );
}
