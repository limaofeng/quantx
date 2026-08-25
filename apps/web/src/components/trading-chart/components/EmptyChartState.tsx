import { BarChart2 } from 'lucide-react';
import React from 'react';

export function EmptyChartState() {
  return (
    <div className="absolute inset-0 z-10 flex flex-col items-center justify-center text-slate-400 dark:text-slate-600 bg-slate-50/50 dark:bg-slate-900/20 backdrop-blur-[1px]">
      <div className="w-16 h-16 rounded-3xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center mb-4 shadow-inner">
        <BarChart2 className="w-8 h-8 opacity-50" />
      </div>
      <h3 className="text-ui-body font-bold uppercase tracking-widest opacity-80">
        No Instrument Selected
      </h3>
      <p className="text-ui-caption font-medium opacity-60 mt-1 max-w-[200px] text-center">
        Select a stock from the list to view its real-time chart
      </p>
    </div>
  );
}
