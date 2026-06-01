import React from 'react';

import { cn } from '@/utils/cn';

import {
  type MainIndicatorType,
  type SubIndicatorType,
} from '../utils/indicators';

interface IndicatorBarProps {
  activeMain: MainIndicatorType[];
  activeSub: SubIndicatorType[];
  onToggleMain: (type: MainIndicatorType) => void;
  onChangeSub: (type: SubIndicatorType) => void;
  className?: string;
}

export function IndicatorBar({
  activeMain,
  activeSub,
  onToggleMain,
  onChangeSub,
  className,
}: IndicatorBarProps) {
  const mainIndicators: MainIndicatorType[] = ['MA', 'EMA', 'BOLL', 'SAR'];
  const subIndicators: SubIndicatorType[] = ['VOL', 'MACD', 'KDJ', 'RSI'];

  return (
    <div
      className={cn(
        'flex items-center gap-4 px-2 py-1 text-xs select-none bg-white dark:bg-[#09090b] border-t border-slate-200 dark:border-slate-800',
        className
      )}
    >
      <div className="flex items-center gap-1">
        <span className="text-muted-foreground mr-1">主图</span>
        {mainIndicators.map(item => (
          <button
            key={item}
            onClick={() => onToggleMain(item)}
            className={cn(
              'px-2 py-0.5 rounded transition-colors',
              activeMain.includes(item)
                ? 'text-blue-500 bg-blue-500/10 font-medium'
                : 'text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200'
            )}
          >
            {item}
          </button>
        ))}
      </div>

      <div className="w-px h-3 bg-slate-300 dark:bg-slate-700 mx-1" />

      <div className="flex items-center gap-1">
        <span className="text-muted-foreground mr-1">副图</span>
        {subIndicators.map(item => (
          <button
            key={item}
            onClick={() => onChangeSub(item)}
            className={cn(
              'px-2 py-0.5 rounded transition-colors',
              activeSub.includes(item)
                ? 'text-blue-500 bg-blue-500/10 font-medium'
                : 'text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200'
            )}
          >
            {item}
          </button>
        ))}
      </div>
    </div>
  );
}
