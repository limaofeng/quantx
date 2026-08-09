import { Database } from 'lucide-react';
import React from 'react';

import { Badge } from '@/components/ui/badge'; // Ensure Badge is imported
import { Card, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';

interface SectorHoldingCardProps {
  sectorName: string;
  holdings: Array<{ name: string; code: string; lastSync: string }>;
}

export function SectorHoldingCard({
  sectorName,
  holdings,
}: SectorHoldingCardProps) {
  return (
    <Card className="border-slate-200/60 dark:border-white/5 bg-white/40 dark:bg-white/[0.02] backdrop-blur-xl rounded-[24px] overflow-hidden shadow-sm hover:shadow-md transition-all h-[360px] flex flex-col group">
      <CardHeader className="py-3 px-4 border-b border-slate-100/50 dark:border-white/5 bg-white/40 dark:bg-white/[0.02]">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-black text-slate-800 dark:text-slate-100 flex items-center gap-2">
            <div className="w-1.5 h-3 rounded-full bg-blue-500" />
            {sectorName}
          </CardTitle>
          <Badge
            variant="secondary"
            className="text-[9px] h-5 px-1.5 font-bold bg-slate-100 dark:bg-white/10 text-slate-500 dark:text-slate-400"
          >
            {holdings.length} symbols
          </Badge>
        </div>
      </CardHeader>
      <ScrollArea className="flex-1">
        <div className="p-2 space-y-1">
          {holdings.map((holding, idx) => (
            <div
              key={idx}
              className="group/item flex items-center justify-between p-2 rounded-xl hover:bg-white/60 dark:hover:bg-white/5 transition-all cursor-default border border-transparent hover:border-slate-100 dark:hover:border-white/5"
            >
              <div className="flex flex-col gap-0.5">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-black text-slate-700 dark:text-slate-200">
                    {holding.name}
                  </span>
                  <span className="text-[9px] font-mono font-bold text-slate-400">
                    {holding.code}
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  <div className="flex items-center gap-1 text-[9px] text-slate-400">
                    <Database size={10} />
                    <span>Cache OK</span>
                  </div>
                  <span className="text-[9px] text-slate-300 dark:text-slate-600">
                    •
                  </span>
                  <span className="text-[9px] text-slate-400">
                    {holding.lastSync}
                  </span>
                </div>
              </div>

              <div className="opacity-0 group-hover/item:opacity-100 transition-opacity">
                <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 shadow-sm shadow-emerald-500/50" />
              </div>
            </div>
          ))}
        </div>
      </ScrollArea>
    </Card>
  );
}
