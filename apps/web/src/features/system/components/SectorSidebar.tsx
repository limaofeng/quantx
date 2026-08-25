import { Database, LayoutGrid, BarChart2 } from 'lucide-react';
import React from 'react';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/utils/cn';

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
  '300SW1': '沪深300 SW',
  '500SW1': '中证500 SW',
  '1000SW1': '中证1000 SW',
  TFG: '主题风格',
  FG: '风格板块',
  OTH: '其他分类',
};

const CLASSIFICATION_GROUPS = [
  { name: 'CORE SECTORS', items: ['SW1', 'SW2', 'SW3'] },
  { name: 'INDEX & MKT', items: ['MKT', 'IDX', '300SW1', '500SW1', '1000SW1'] },
  { name: 'THEMATIC', items: ['GN', 'TGN', 'TFG', 'FG'] },
  { name: 'REGULATORY', items: ['CSRC', 'CSRC1'] },
  { name: 'OTHERS', items: ['EXCH', 'DY', 'HKSW1', 'HKSW2', 'HKSW3', 'OTH'] },
];

interface SectorSidebarProps {
  activeTab: string;
  statsCounts: Record<string, number>;
  onTabChange: (tab: string) => void;
}

export function SectorSidebar({
  activeTab,
  statsCounts,
  onTabChange,
}: SectorSidebarProps) {
  const totalCount = Object.values(statsCounts).reduce((a, b) => a + b, 0);

  return (
    <div className="w-full lg:w-64 shrink-0 flex flex-col gap-3">
      <Card className="flex-1 border-slate-200/60 dark:border-white/5 bg-white/40 dark:bg-white/[0.02] backdrop-blur-xl rounded-panel overflow-hidden shadow-sm flex flex-col min-h-0">
        <div className="px-ui-section py-3 border-b border-slate-100/50 dark:border-white/5">
          <span className="text-ui-caption font-black text-slate-400 dark:text-slate-500 uppercase tracking-widest flex items-center gap-2">
            <LayoutGrid size={10} />
            Categories
          </span>
        </div>

        <ScrollArea className="flex-1 px-2 py-2">
          <div className="space-y-ui-section">
            <Button
              variant={activeTab === 'all' ? 'secondary' : 'ghost'}
              className={cn(
                'w-full justify-start gap-2 h-control-compact text-ui-caption rounded-lg transition-all',
                activeTab === 'all'
                  ? 'bg-blue-600/10 text-blue-600 dark:text-blue-400 font-bold'
                  : 'hover:bg-slate-100 dark:hover:bg-white/5 text-slate-600 dark:text-slate-400'
              )}
              onClick={() => onTabChange('all')}
            >
              <BarChart2 className="w-3.5 h-3.5" />
              All Sectors
              <span className="ml-auto text-ui-micro font-mono opacity-50">
                {totalCount}
              </span>
            </Button>

            {CLASSIFICATION_GROUPS.map(group => (
              <div key={group.name} className="space-y-0.5">
                <h3 className="px-2 mb-1 text-ui-micro font-black text-slate-300 dark:text-slate-600 uppercase tracking-wider">
                  {group.name}
                </h3>
                {group.items.map(item => (
                  <Button
                    key={item}
                    variant={activeTab === item ? 'secondary' : 'ghost'}
                    className={cn(
                      'w-full justify-start gap-2 h-control-compact text-ui-caption px-2 rounded-lg transition-all',
                      activeTab === item
                        ? 'bg-blue-600/10 text-blue-600 dark:text-blue-400 font-bold'
                        : 'text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 hover:bg-slate-50 dark:hover:bg-white/5'
                    )}
                    onClick={() => onTabChange(item)}
                  >
                    <div
                      className={cn(
                        'w-1 h-1 rounded-full transition-all shrink-0',
                        activeTab === item
                          ? 'bg-blue-500 scale-125'
                          : 'bg-slate-300 dark:bg-slate-700'
                      )}
                    />
                    <span className="truncate flex-1 text-left">
                      {CLASSIFICATION_LABELS[item] || item}
                    </span>
                    {statsCounts[item] > 0 && (
                      <span className="text-ui-micro font-mono opacity-40 tabular-nums">
                        {statsCounts[item]}
                      </span>
                    )}
                  </Button>
                ))}
              </div>
            ))}
          </div>
        </ScrollArea>
      </Card>

      {/* Quick Metrics (Compact) */}
      <div className="relative group overflow-hidden rounded-panel bg-gradient-to-br from-blue-600 to-indigo-700 p-ui-section text-white shadow-lg shadow-blue-500/20">
        <div className="relative z-10 flex items-center justify-between">
          <div>
            <p className="text-ui-micro font-bold uppercase tracking-widest opacity-70 mb-0.5">
              Total Count
            </p>
            <p className="text-ui-display font-black tracking-tighter">
              {totalCount.toLocaleString()}
            </p>
          </div>
          <Database className="text-white/20 w-8 h-8 rotate-12" />
        </div>
      </div>
    </div>
  );
}
