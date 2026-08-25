import { Activity, BarChart2 } from 'lucide-react';
import React from 'react';

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/utils/cn';

import { PERIODS, MINUTE_PERIODS } from '../utils/options';

interface ChartHeaderProps {
  activePeriod: string;
  isTimeMode: boolean;
  lastKLinePeriod: string;
  onModeSwitch: (mode: 'time' | 'kline') => void;
  onKLinePeriodChange: (value: string) => void;
  onTimePeriodChange: (value: string) => void;
}

export function ChartHeader({
  activePeriod,
  isTimeMode,
  lastKLinePeriod,
  onModeSwitch,
  onKLinePeriodChange,
  onTimePeriodChange,
}: ChartHeaderProps) {
  return (
    <div className="absolute top-3 left-3 z-20 flex items-center gap-2">
      <div className="h-8 p-1 rounded-xl bg-white/80 dark:bg-slate-900/80 backdrop-blur-md border border-slate-200/50 dark:border-slate-800/50 flex items-center shadow-lg shadow-black/5">
        <button
          onClick={() => onTimePeriodChange('1m_line')}
          className={cn(
            'px-3 h-full rounded-lg text-ui-caption font-bold transition-all duration-300 flex items-center gap-1.5',
            activePeriod === '1m_line'
              ? 'bg-blue-600 text-white shadow-sm'
              : 'text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-white/5'
          )}
        >
          <Activity className="w-3 h-3" />
          分时
        </button>
        <button
          onClick={() => onTimePeriodChange('5d_line')}
          className={cn(
            'px-3 h-full rounded-lg text-ui-caption font-bold transition-all duration-300 flex items-center gap-1.5',
            activePeriod === '5d_line'
              ? 'bg-blue-600 text-white shadow-sm'
              : 'text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-white/5'
          )}
        >
          五日
        </button>

        <div className="w-px h-3 bg-slate-200 dark:bg-slate-700/50 mx-1" />

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              className={cn(
                'px-3 h-full rounded-lg text-ui-caption font-bold transition-all duration-300 flex items-center gap-1.5',
                !isTimeMode
                  ? 'bg-blue-600 text-white shadow-sm'
                  : 'text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-white/5'
              )}
              onClick={() => onModeSwitch('kline')}
            >
              <BarChart2 className="w-3 h-3" />
              {PERIODS.find(p => p.value === lastKLinePeriod)?.label ||
                MINUTE_PERIODS.find(p => p.value === lastKLinePeriod)?.label ||
                '日K'}
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="start"
            className="w-[120px] max-h-[300px] overflow-y-auto custom-scrollbar"
          >
            <div className="px-2 py-1.5 text-ui-caption uppercase font-bold text-muted-foreground">
              常用周期
            </div>
            {PERIODS.map(period => (
              <DropdownMenuItem
                key={period.value}
                className={cn(
                  'text-ui-label cursor-pointer',
                  activePeriod === period.value &&
                    'text-blue-600 bg-blue-50 dark:bg-blue-600/10'
                )}
                onClick={() => onKLinePeriodChange(period.value)}
              >
                {period.label}
              </DropdownMenuItem>
            ))}
            <div className="px-2 py-1.5 text-ui-caption uppercase font-bold text-muted-foreground mt-2 border-t pt-2">
              分钟周期
            </div>
            {MINUTE_PERIODS.map(period => (
              <DropdownMenuItem
                key={period.value}
                className={cn(
                  'text-ui-label cursor-pointer',
                  activePeriod === period.value &&
                    'text-blue-600 bg-blue-50 dark:bg-blue-600/10'
                )}
                onClick={() => onKLinePeriodChange(period.value)}
              >
                {period.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}
