import { Database } from 'lucide-react';
import React from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from '@/components/ui/sheet';

import type { Sector } from './SectorTable';

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

interface SectorDetailDrawerProps {
  sector: Sector | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SectorDetailDrawer({
  sector,
  open,
  onOpenChange,
}: SectorDetailDrawerProps) {
  if (!sector) return null;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-[500px] sm:w-[600px] p-0 border-l border-slate-200 dark:border-white/5 shadow-none"
      >
        <div className="flex flex-col h-full bg-white dark:bg-slate-950">
          <div className="p-ui-empty space-y-ui-section shrink-0 relative overflow-hidden">
            <div className="absolute top-0 right-0 w-64 h-64 bg-blue-500/5 rounded-full -mr-32 -mt-32 blur-[100px]" />
            <SheetHeader>
              <div className="flex items-center justify-between mb-4">
                <Badge className="bg-blue-600/10 text-blue-600 border-none font-black text-ui-micro uppercase tracking-widest px-3">
                  {CLASSIFICATION_LABELS[sector.classification]}
                </Badge>
                <span className="text-ui-caption font-mono font-bold text-slate-400">
                  {sector.code}
                </span>
              </div>
              <SheetTitle className="text-5xl font-black text-slate-900 dark:text-white tracking-tighter italic">
                {sector.name}
              </SheetTitle>
              <SheetDescription className="text-ui-label font-medium leading-relaxed text-slate-400 pt-2 opacity-80">
                {sector.description ||
                  `${sector.name}板块成分股列表及其基本行情概览。`}
              </SheetDescription>
            </SheetHeader>

            <div className="grid grid-cols-2 gap-ui-section pt-4">
              <div className="p-ui-panel rounded-panel bg-slate-50 dark:bg-white/[0.03] border border-slate-100 dark:border-white/5">
                <p className="text-ui-micro font-black text-slate-400 uppercase tracking-widest mb-1.5 opacity-60">
                  成分股数量
                </p>
                <h4 className="text-ui-display-lg font-black text-blue-600">
                  {(sector.stockCodes || []).length}
                </h4>
              </div>
              <div className="p-ui-panel rounded-panel bg-slate-50 dark:bg-white/[0.03] border border-slate-100 dark:border-white/5">
                <p className="text-ui-micro font-black text-slate-400 uppercase tracking-widest mb-1.5 opacity-60">
                  数据源
                </p>
                <div className="flex items-center gap-2">
                  <Database size={20} className="text-blue-500" />
                  <span className="text-ui-heading font-black text-slate-800 dark:text-slate-200 uppercase tracking-tighter">
                    QuantX DB
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div className="flex-1 flex flex-col overflow-hidden px-ui-section pb-10">
            <div className="flex items-center justify-between py-ui-panel">
              <h4 className="text-ui-caption font-black text-slate-900 dark:text-white uppercase tracking-[0.2em] flex items-center gap-2">
                <div className="w-1.5 h-1.5 bg-blue-600 rounded-full" />
                Components List
              </h4>
            </div>
            <ScrollArea className="flex-1">
              <div className="grid grid-cols-3 gap-2">
                {(sector.stockCodes || []).map((code: string) => (
                  <div
                    key={code}
                    className="p-3 rounded-panel bg-slate-50 dark:bg-white/[0.03] border border-slate-100 dark:border-white/5 flex flex-col items-center justify-center hover:bg-blue-600 hover:text-white transition-all cursor-default group"
                  >
                    <span className="text-ui-label font-black font-mono">
                      {code}
                    </span>
                  </div>
                ))}
              </div>
            </ScrollArea>
          </div>

          <div className="p-ui-empty border-t border-slate-100 dark:border-white/5 flex gap-ui-section bg-slate-50/50 dark:bg-white/[0.01]">
            <Button className="flex-1 h-16 rounded-panel bg-blue-600 hover:bg-blue-700 text-white font-black shadow-none shadow-blue-500/10 transition-all text-ui-body uppercase tracking-widest">
              可视化图表
            </Button>
            <Button
              variant="ghost"
              className="w-24 h-16 rounded-panel font-black text-slate-500 uppercase tracking-widest text-ui-label"
              onClick={() => onOpenChange(false)}
            >
              Close
            </Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
