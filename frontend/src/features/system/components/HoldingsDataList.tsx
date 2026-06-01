import { Database, Clock } from 'lucide-react';
import React from 'react';

import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

interface HoldingsDataListProps {
  holdings: any[];
}

export function HoldingsDataList({ holdings }: HoldingsDataListProps) {
  return (
    <div className="flex flex-col h-full">
      <Table wrapperClassName="flex-1 custom-scrollbar" className="relative">
        <TableHeader className="sticky top-0 z-10">
          <TableRow className="hover:bg-transparent border-none">
            <TableHead className="w-[100px] h-9 pl-6 text-[9px] font-black uppercase tracking-widest text-slate-400 bg-slate-50/95 dark:bg-[#0c1120]/95 backdrop-blur-md sticky top-0">
              代码
            </TableHead>
            <TableHead className="w-[120px] h-9 text-[9px] font-black uppercase tracking-widest text-slate-400 bg-slate-50/95 dark:bg-[#0c1120]/95 backdrop-blur-md sticky top-0">
              名称
            </TableHead>
            <TableHead className="w-[120px] h-9 text-[9px] font-black uppercase tracking-widest text-slate-400 bg-slate-50/95 dark:bg-[#0c1120]/95 backdrop-blur-md sticky top-0">
              所属板块
            </TableHead>
            <TableHead className="h-9 text-[9px] font-black uppercase tracking-widest text-slate-400 bg-slate-50/95 dark:bg-[#0c1120]/95 backdrop-blur-md sticky top-0">
              缓存状态 (TICK / 1M / DAY)
            </TableHead>
            <TableHead className="w-[120px] h-9 text-right pr-6 text-[9px] font-black uppercase tracking-widest text-slate-400 bg-slate-50/95 dark:bg-[#0c1120]/95 backdrop-blur-md sticky top-0">
              更新时间
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {holdings.map((item, idx) => (
            <TableRow
              key={idx}
              className="h-10 border-b border-slate-50 dark:border-white/5 hover:bg-slate-50/50 dark:hover:bg-white/[0.03] transition-colors group cursor-default"
            >
              <TableCell className="pl-6 py-0 font-mono text-[10px] font-bold text-slate-500 dark:text-slate-400">
                {item.code}
              </TableCell>
              <TableCell className="py-0">
                <span className="text-xs font-bold text-slate-700 dark:text-slate-200 group-hover:text-blue-600 transition-colors">
                  {item.name}
                </span>
              </TableCell>
              <TableCell className="py-0">
                <Badge
                  variant="secondary"
                  className="h-5 px-1.5 text-[9px] font-bold bg-slate-100 dark:bg-white/10 text-slate-500 rounded-md"
                >
                  {item.sector}
                </Badge>
              </TableCell>
              <TableCell className="py-0">
                <div className="flex items-center gap-2">
                  <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-emerald-500/5 border border-emerald-500/10">
                    <div className="w-1 h-1 rounded-full bg-emerald-500" />
                    <span className="text-[9px] font-mono text-emerald-600 dark:text-emerald-400">
                      Full
                    </span>
                  </div>
                  <span className="text-[9px] text-slate-300 dark:text-slate-700 font-mono opacity-50 truncate max-w-[200px]">
                    {item.cacheStatus.tickRange}
                  </span>
                </div>
              </TableCell>
              <TableCell className="text-right pr-6 py-0">
                <div className="flex items-center justify-end gap-1.5 text-slate-400">
                  <Clock size={10} />
                  <span className="text-[9px] font-mono">{item.lastSync}</span>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
