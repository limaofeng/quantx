import { Database, Server } from 'lucide-react';
import React from 'react';

interface MetricProps {
  label: string;
  value: string;
  subValue?: string;
  icon: React.ElementType;
}

function Metric({ label, value, subValue, icon: Icon }: MetricProps) {
  return (
    <div className="flex items-center gap-3">
      <div className="w-8 h-8 rounded-lg bg-indigo-500/10 dark:bg-indigo-500/20 flex items-center justify-center text-indigo-600 dark:text-indigo-400">
        <Icon className="w-4 h-4" />
      </div>
      <div className="flex flex-col">
        <p className="text-[10px] font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
          {label}
        </p>
        <div className="flex items-baseline gap-1.5 leading-none">
          <h3 className="text-sm font-black text-slate-900 dark:text-white tracking-tight">
            {value}
          </h3>
          {subValue && (
            <span className="text-[10px] font-mono text-slate-400">
              {subValue}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

export function DataPortalHeader() {
  return (
    <div className="flex items-center gap-8">
      <Metric label="全市场覆盖" value="5,324" subValue="家" icon={Database} />
      <div className="h-8 w-[1px] bg-border/40" />
      <Metric label="总数据量" value="12.4" subValue="GB" icon={Server} />
    </div>
  );
}
