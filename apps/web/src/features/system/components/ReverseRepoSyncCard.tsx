import { Coins, Clock, ChevronRight } from 'lucide-react';
import React from 'react';
import { useLocation } from 'wouter';

import { Badge } from '@/components/ui/badge';

export function ReverseRepoSyncCard() {
  const [, setLocation] = useLocation();

  return (
    <div
      className="h-full flex flex-col p-ui-section rounded-panel border border-slate-200/40 dark:border-slate-800/40 bg-gradient-to-br from-blue-50/50 to-teal-50/50 dark:from-blue-950/10 dark:to-teal-900/10 overflow-hidden relative group cursor-pointer transition-all hover:bg-blue-50/80 dark:hover:bg-blue-950/20 shadow-sm hover:shadow-md"
      onClick={() => setLocation('/settings/data/reverse-repo')}
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-4 z-10">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-panel bg-blue-600/10 text-blue-700 dark:text-blue-500 ring-1 ring-inset ring-black/5 dark:ring-white/10">
            <Coins className="w-5 h-5" />
          </div>
          <div>
            <h3 className="font-bold text-ui-title text-slate-800 dark:text-slate-100">
              国债逆回购
            </h3>
            <p className="text-ui-caption text-slate-500 dark:text-slate-400 font-medium mt-0.5">
              Reverse Repo
            </p>
          </div>
        </div>

        <Badge
          variant="outline"
          className="bg-emerald-500/5 text-emerald-600 border-emerald-500 gap-1 flex items-center border-opacity-20"
        >
          <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
          <span className="text-ui-caption">监控中</span>
        </Badge>
      </div>

      {/* Info Content */}
      <div className="flex-1 flex flex-col justify-end z-10">
        <div className="flex flex-col mb-4">
          <span className="text-ui-display-lg font-black text-slate-800 dark:text-slate-100 line-height-1">
            9
          </span>
          <span className="text-ui-caption font-bold text-slate-500 uppercase tracking-wider">
            个活跃品种
          </span>
        </div>

        <div className="pt-3 border-t border-blue-300/50 dark:border-blue-900/50 flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-slate-500 dark:text-slate-400">
            <Clock size={12} />
            <span className="text-ui-caption font-mono whitespace-nowrap">
              最近同步: 2026/1/19
            </span>
          </div>
          <div className="flex items-center gap-1 text-ui-caption font-semibold text-blue-700 dark:text-blue-500 opacity-0 group-hover:opacity-100 transition-all -translate-x-2 group-hover:translate-x-0">
            管理回购
            <ChevronRight size={12} />
          </div>
        </div>
      </div>

      {/* Decorative Background */}
      <div className="absolute -right-8 -bottom-8 w-32 h-32 bg-blue-600/10 rounded-full blur-3xl group-hover:bg-blue-600/20 transition-all duration-500 opacity-20" />
    </div>
  );
}
