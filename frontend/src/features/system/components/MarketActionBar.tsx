import { ArrowLeft } from 'lucide-react';
import React from 'react';

import { Button } from '@/components/ui/button';
import { MARKET_TAB_LABELS } from '@/features/system/constants/marketMenu';

import { SyncControlPanel, type DeploymentStatus } from './SyncControlPanel';

interface MarketActionBarProps {
  activeTab: string;
  syncDeployment: DeploymentStatus | undefined;
  isSyncing: boolean;
  onBack: () => void;
  onShowHistory: () => void;
  onSync: () => void;
}

export function MarketActionBar({
  activeTab,
  syncDeployment,
  isSyncing,
  onBack,
  onShowHistory,
  onSync,
}: MarketActionBarProps) {
  return (
    <div className="flex flex-col md:flex-row items-center justify-between gap-4 py-2">
      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 rounded-lg bg-white/50 dark:bg-white/5 border border-slate-200/60 dark:border-white/5 shadow-sm hover:scale-105 active:scale-95 transition-all backdrop-blur-sm"
          onClick={onBack}
        >
          <ArrowLeft className="w-4 h-4 text-slate-600 dark:text-slate-400" />
        </Button>
        <div>
          <h1 className="text-lg font-black text-slate-900 dark:text-white tracking-tight leading-none">
            {MARKET_TAB_LABELS[activeTab] || '全市场数据门户'}
          </h1>
          <p className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest mt-0.5 opacity-80">
            Comprehensive Market Data & Control Center
          </p>
        </div>
      </div>

      <SyncControlPanel
        deployment={syncDeployment}
        isSyncing={isSyncing}
        defaultFlowName="市场同步"
        onShowHistory={onShowHistory}
        onSync={onSync}
      />
    </div>
  );
}
