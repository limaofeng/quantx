import { ArrowLeft } from 'lucide-react';
import React from 'react';

import { Button } from '@/components/ui/button';

import { DeploymentSyncControl } from './DeploymentSyncControl';

const CLASSIFICATION_LABELS: Record<string, string> = {
  all: '全部板块',
  SW1: '申万一级',
  SW2: '申万二级',
  SW3: '申万三级',
  THY1: '行业一级',
  THY2: '行业二级',
  THY3: '行业三级',
  GN: '概念板块',
  TGN: '主题概念',
  DY: '地域板块',
  CSRC: '证监会大类',
  CSRC1: '证监会行业',
  MKT: '市场/宽基',
  IDX: '指数系列',
  EXCH: '交易所/资产',
  HKSW1: '港股一级',
  HKSW2: '港股二级',
  HKSW3: '港股三级',
  '300SW1': '沪深300申万',
  '500SW1': '中证500申万',
  '1000SW1': '中证1000申万',
  TFG: '主题风格',
  FG: '风格板块',
  OTH: '其他分类',
};

interface SectorActionBarProps {
  activeTab: string;
  totalCount: number;
  currentPage: number;
  totalPages: number;
  onBack: () => void;
}

export function SectorActionBar({
  activeTab,
  totalCount,
  currentPage,
  totalPages,
  onBack,
}: SectorActionBarProps) {
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
            {activeTab === 'all'
              ? '全市场板块'
              : CLASSIFICATION_LABELS[activeTab] || activeTab}
          </h1>
          <p className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest mt-0.5 opacity-80">
            {totalCount} ITEMS • PAGE {currentPage}/{totalPages || 1}
          </p>
        </div>
      </div>

      <DeploymentSyncControl
        deploymentName="sector-data-sync"
        defaultFlowName="板块同步"
        successMessage="同步已启动"
      />
    </div>
  );
}
