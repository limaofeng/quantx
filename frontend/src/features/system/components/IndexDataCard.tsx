import {
  Activity,
  Clock,
  TrendingUp,
  TrendingDown,
  AlertCircle,
} from 'lucide-react';
import React from 'react';

import { cn } from '@/utils/cn';

interface IndexDataCardProps {
  name: string;
  code: string;
  price: string;
  change: string;
  changePercent: string;
  status?: 'normal' | 'warning' | 'error';
  latency?: string;
  lastSync?: string;
}

export function IndexDataCard({
  name,
  code,
  price,
  change,
  changePercent,
  status = 'normal',
  lastSync = 'End of Day',
}: IndexDataCardProps) {
  const isPositive = parseFloat(change) >= 0;

  return (
    <div
      className={cn(
        'h-full flex flex-col justify-between p-4 rounded-xl border backdrop-blur-sm transition-all relative overflow-hidden',
        status === 'normal' &&
          'border-slate-200/40 dark:border-slate-800/40 bg-white/40 dark:bg-slate-900/40 hover:bg-white/60 dark:hover:bg-slate-900/60',
        status === 'warning' &&
          'border-amber-200/60 dark:border-amber-900/60 bg-amber-50/40 dark:bg-amber-900/10',
        status === 'error' &&
          'border-red-200/60 dark:border-red-900/60 bg-red-50/30 dark:bg-red-950/20 animate-pulse-subtle' // animate-pulse-subtle needs to be defined or use standard pulse
      )}
    >
      {/* Top Status Bar */}
      <div className="flex justify-between items-start mb-2">
        <div>
          <h3 className="text-sm font-bold text-slate-700 dark:text-slate-200 flex items-center gap-2">
            {name}
            {status === 'error' && (
              <span className="flex h-2 w-2 rounded-full bg-red-500 animate-pulse shadow-[0_0_8px_rgba(239,68,68,0.6)]" />
            )}
            {status === 'warning' && (
              <span className="flex h-2 w-2 rounded-full bg-amber-500 shadow-[0_0_8px_rgba(245,158,11,0.6)]" />
            )}
            {status === 'normal' && (
              <span className="flex h-2 w-2 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]" />
            )}
          </h3>
          <p className="text-[10px] text-slate-500 font-mono mt-0.5">{code}</p>
        </div>
      </div>

      {/* Main Data View */}
      <div className="mt-2">
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-black tracking-tight text-slate-800 dark:text-slate-200">
            {price}
          </span>
          <span
            className={cn(
              'text-xs font-semibold',
              isPositive ? 'text-red-500' : 'text-green-500'
            )}
          >
            {changePercent}
          </span>
        </div>
        <div className="mt-2 text-[10px] text-slate-400 flex justify-between items-center border-t border-slate-200/30 dark:border-slate-800/30 pt-2">
          <span>Last Sync:</span>
          <span
            className={cn(
              'font-mono',
              status === 'error'
                ? 'text-red-500 font-bold'
                : 'text-slate-600 dark:text-slate-300'
            )}
          >
            {lastSync}
          </span>
        </div>
        {status === 'error' && (
          <div className="mt-1 text-[10px] text-red-500 font-bold flex items-center gap-1">
            <AlertCircle className="w-3 h-3" /> Connection Lost
          </div>
        )}
      </div>

      {/* Background Decor */}
      <div
        className={cn(
          'absolute -right-6 -bottom-6 w-24 h-24 rounded-full blur-3xl transition-colors opacity-20',
          status === 'normal' && 'bg-emerald-500',
          status === 'error' && 'bg-red-600 opacity-30',
          status === 'warning' && 'bg-amber-500'
        )}
      />
    </div>
  );
}
