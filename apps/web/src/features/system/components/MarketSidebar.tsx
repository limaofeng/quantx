import { Database, LayoutGrid } from 'lucide-react';
import React from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { MARKET_MENU_ITEMS } from '@/features/system/constants/marketMenu';
import { cn } from '@/utils/cn';

interface MarketSidebarProps {
  activeTab: string;
  onTabChange: (tab: string) => void;
}

export function MarketSidebar({ activeTab, onTabChange }: MarketSidebarProps) {
  return (
    <div className="flex min-h-0 w-full shrink-0 flex-col gap-3 lg:w-64">
      <Card className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-panel border-slate-200/60 bg-white/40 shadow-sm backdrop-blur-xl dark:border-white/5 dark:bg-white/[0.02]">
        <CardHeader className="pb-3 px-ui-panel pt-6 border-b border-slate-100/50 dark:border-white/5 bg-slate-50/50 dark:bg-transparent">
          <CardTitle className="text-ui-caption font-black text-slate-400 dark:text-slate-500 uppercase tracking-[0.2em] flex items-center justify-between">
            数据模块
            <LayoutGrid size={12} className="opacity-40" />
          </CardTitle>
        </CardHeader>
        <ScrollArea className="min-h-[280px] flex-1 px-3 py-ui-section lg:min-h-0">
          <div className="space-y-1">
            {MARKET_MENU_ITEMS.map(item => (
              <Button
                key={item.id}
                variant={activeTab === item.id ? 'secondary' : 'ghost'}
                className={cn(
                  'w-full justify-start gap-3 h-control-compact text-ui-label rounded-panel transition-all',
                  activeTab === item.id
                    ? 'bg-indigo-600/10 text-indigo-600 dark:text-indigo-400 font-bold shadow-sm'
                    : 'text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-white/5'
                )}
                onClick={() => onTabChange(item.id)}
              >
                <item.icon className="w-4 h-4" />
                {item.label}
                {activeTab === item.id && (
                  <div className="ml-auto w-1.5 h-1.5 rounded-full bg-indigo-500 ring-2 ring-indigo-500/20" />
                )}
              </Button>
            ))}
          </div>
        </ScrollArea>
      </Card>

      {/* Quick Metrics in Sidebar */}
      <Card className="border-none bg-gradient-to-br from-indigo-600 to-purple-700 shadow-none shadow-indigo-500/20 rounded-panel p-ui-section text-white overflow-hidden relative group">
        <div className="relative z-10">
          <p className="text-ui-caption font-black uppercase tracking-widest opacity-60 mb-1">
            证券总数
          </p>
          <h4 className="text-ui-display font-black tracking-tighter mb-4">
            5,324
          </h4>
          <div className="flex items-center gap-2">
            <span className="px-2 py-0.5 rounded-lg bg-white/20 text-ui-micro font-bold">
              全A + ETF
            </span>
          </div>
        </div>
        <div className="absolute top-0 right-0 p-ui-section opacity-10 group-hover:scale-110 transition-transform">
          <Database size={80} strokeWidth={1} />
        </div>
      </Card>
    </div>
  );
}
