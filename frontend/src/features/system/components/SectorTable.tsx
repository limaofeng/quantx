import { Copy, Eye, Search } from 'lucide-react';
import React from 'react';

import { StudioMenu, useStudioMenu } from '@/components/studio-workbench';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { cn } from '@/utils/cn';

interface Sector {
  id: string;
  name: string;
  code: string;
  classification: string;
  market: string;
  description: string;
  level: number;
  parentId?: string;
  stockCodes: string[];
}

interface SectorTableProps {
  sectors: Sector[];
  fetching: boolean;
  totalCount: number;
  currentPage: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onSectorClick: (sector: Sector) => void;
}

type SectorTableMenuPayload =
  | { columnId: string; kind: 'column'; label: string }
  | { kind: 'row'; sector: Sector };

function copyText(value: string | number | undefined | null) {
  if (value === undefined || value === null || value === '') return;
  void navigator.clipboard?.writeText(String(value));
}

export function SectorTable({
  sectors,
  fetching,
  totalCount,
  currentPage,
  pageSize,
  onPageChange,
  onSectorClick,
}: SectorTableProps) {
  const totalPages = Math.ceil(totalCount / pageSize);
  const { closeMenu, menu, openAtPointer } =
    useStudioMenu<SectorTableMenuPayload>();

  return (
    <Card className="flex-1 border-slate-200/60 dark:border-white/5 bg-white/40 dark:bg-white/[0.02] backdrop-blur-xl rounded-[24px] overflow-hidden shadow-sm flex flex-col min-h-0">
      <Table wrapperClassName="flex-1 custom-scrollbar" className="relative">
        <TableHeader className="sticky top-0 z-10">
          <TableRow className="hover:bg-transparent border-none">
            <TableHead
              className="w-[120px] h-9 pl-6 text-[9px] font-black uppercase tracking-widest text-slate-400 bg-slate-50/95 dark:bg-[#0c1120]/95 backdrop-blur-md sticky top-0"
              onContextMenu={event =>
                openAtPointer(event, {
                  kind: 'column',
                  columnId: 'code',
                  label: 'CODE',
                })
              }
            >
              CODE
            </TableHead>
            <TableHead
              className="h-9 text-[9px] font-black uppercase tracking-widest text-slate-400 bg-slate-50/95 dark:bg-[#0c1120]/95 backdrop-blur-md sticky top-0"
              onContextMenu={event =>
                openAtPointer(event, {
                  kind: 'column',
                  columnId: 'name',
                  label: 'SECTOR NAME',
                })
              }
            >
              SECTOR NAME
            </TableHead>
            <TableHead
              className="w-[60px] h-9 text-[9px] font-black uppercase tracking-widest text-slate-400 bg-slate-50/95 dark:bg-[#0c1120]/95 backdrop-blur-md sticky top-0"
              onContextMenu={event =>
                openAtPointer(event, {
                  kind: 'column',
                  columnId: 'market',
                  label: 'MKT',
                })
              }
            >
              MKT
            </TableHead>
            <TableHead
              className="w-[80px] h-9 text-[9px] font-black uppercase tracking-widest text-slate-400 bg-slate-50/95 dark:bg-[#0c1120]/95 backdrop-blur-md sticky top-0"
              onContextMenu={event =>
                openAtPointer(event, {
                  kind: 'column',
                  columnId: 'level',
                  label: 'LEVEL',
                })
              }
            >
              LEVEL
            </TableHead>
            <TableHead
              className="w-[80px] h-9 text-right pr-6 text-[9px] font-black uppercase tracking-widest text-slate-400 bg-slate-50/95 dark:bg-[#0c1120]/95 backdrop-blur-md sticky top-0"
              onContextMenu={event =>
                openAtPointer(event, {
                  kind: 'column',
                  columnId: 'stocks',
                  label: 'STOCKS',
                })
              }
            >
              STOCKS
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {fetching ? (
            Array(10)
              .fill(0)
              .map((_, i) => (
                <TableRow
                  key={i}
                  className="h-10 border-b border-slate-50 dark:border-white/5"
                >
                  <TableCell colSpan={5} className="px-6 animate-pulse">
                    <div className="h-3 bg-slate-100 dark:bg-white/5 rounded-lg w-full" />
                  </TableCell>
                </TableRow>
              ))
          ) : sectors.length === 0 ? (
            <TableRow>
              <TableCell colSpan={5} className="h-[300px]">
                <div className="flex flex-col items-center justify-center opacity-30 grayscale gap-3">
                  <div className="p-6 bg-slate-100 dark:bg-white/5 rounded-full">
                    <Search size={32} />
                  </div>
                  <p className="text-xs font-black uppercase tracking-widest">
                    No sectors found
                  </p>
                </div>
              </TableCell>
            </TableRow>
          ) : (
            sectors.map(sector => (
              <TableRow
                key={sector.id}
                className="h-10 group cursor-pointer hover:bg-slate-50/50 dark:hover:bg-white/[0.03] transition-all border-b border-slate-50 dark:border-white/5"
                onClick={() => onSectorClick(sector)}
                onContextMenu={event =>
                  openAtPointer(event, { kind: 'row', sector })
                }
              >
                <TableCell className="pl-6 py-0 font-mono text-[10px] font-bold text-slate-400 dark:text-slate-500 group-hover:text-blue-500 transition-colors">
                  {sector.code}
                </TableCell>
                <TableCell className="py-0">
                  <div className="flex flex-col justify-center">
                    <span className="font-bold text-xs text-slate-700 dark:text-slate-200 group-hover:translate-x-1 transition-transform duration-300">
                      {sector.name}
                    </span>
                    <span className="text-[9px] text-slate-400/80 dark:text-slate-500 line-clamp-1 opacity-0 group-hover:opacity-100 transition-opacity absolute -bottom-3 left-0 pointer-events-none">
                      {sector.description}
                    </span>
                  </div>
                </TableCell>
                <TableCell className="py-0">
                  <Badge
                    variant="outline"
                    className={cn(
                      'text-[9px] h-4 px-1.5 font-bold border-none rounded-sm min-w-[24px] justify-center',
                      sector.market === 'HK'
                        ? 'bg-orange-500/10 text-orange-600'
                        : 'bg-blue-500/10 text-blue-600'
                    )}
                  >
                    {sector.market || 'CN'}
                  </Badge>
                </TableCell>
                <TableCell className="py-0">
                  <div className="flex items-center gap-1.5">
                    <div
                      className={cn(
                        'w-1.5 h-1.5 rounded-full',
                        sector.level === 1
                          ? 'bg-blue-500'
                          : sector.level === 2
                            ? 'bg-orange-500'
                            : 'bg-emerald-500'
                      )}
                    />
                    <span className="text-[10px] font-bold text-slate-500 uppercase">
                      L{sector.level}
                    </span>
                  </div>
                </TableCell>
                <TableCell className="text-right pr-6 py-0">
                  <span className="text-xs font-bold text-slate-900 dark:text-slate-100 font-mono tabular-nums opacity-60">
                    {(sector.stockCodes || []).length}
                  </span>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>

      {/* Compact Pagination */}
      <div className="px-4 py-2 bg-slate-50/50 dark:bg-white/[0.01] border-t border-slate-100 dark:border-white/5 flex items-center justify-between shrink-0 h-10">
        <div className="text-[9px] font-bold text-slate-400 uppercase tracking-wider">
          {totalCount > 0 ? (currentPage - 1) * pageSize + 1 : 0}-
          {Math.min(currentPage * pageSize, totalCount)} of {totalCount}
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            disabled={currentPage === 1 || fetching}
            onClick={() => onPageChange(Math.max(1, currentPage - 1))}
            className="rounded-lg h-6 px-2 text-[10px] font-bold hover:bg-blue-600/10 hover:text-blue-600"
          >
            Prev
          </Button>
          <div className="flex items-center px-2">
            <span className="text-[10px] font-mono font-bold text-slate-500">
              {currentPage} / {Math.max(1, totalPages)}
            </span>
          </div>
          <Button
            variant="ghost"
            size="sm"
            disabled={
              currentPage === totalPages || totalPages === 0 || fetching
            }
            onClick={() => onPageChange(Math.min(totalPages, currentPage + 1))}
            className="rounded-lg h-6 px-2 text-[10px] font-bold hover:bg-blue-600/10 hover:text-blue-600"
          >
            Next
          </Button>
        </div>
      </div>

      <StudioMenu
        ariaLabel="板块表菜单"
        menu={menu}
        onClose={closeMenu}
        width={196}
        items={[
          {
            id: 'open-sector',
            label: '打开板块详情',
            icon: <Eye size={14} />,
            disabled: menu?.payload?.kind !== 'row',
            onSelect: () => {
              if (menu?.payload?.kind === 'row') {
                onSectorClick(menu.payload.sector);
              }
            },
          },
          {
            id: 'copy-code',
            label: '复制板块代码',
            icon: <Copy size={14} />,
            disabled: menu?.payload?.kind !== 'row',
            onSelect: () => {
              if (menu?.payload?.kind === 'row') {
                copyText(menu.payload.sector.code);
              }
            },
          },
          {
            id: 'copy-name',
            label: '复制板块名称',
            icon: <Copy size={14} />,
            disabled: menu?.payload?.kind !== 'row',
            onSelect: () => {
              if (menu?.payload?.kind === 'row') {
                copyText(menu.payload.sector.name);
              }
            },
          },
          { id: 'sep-column', type: 'separator' },
          {
            id: 'copy-column-name',
            label: '复制列名',
            icon: <Copy size={14} />,
            disabled: menu?.payload?.kind !== 'column',
            onSelect: () => {
              if (menu?.payload?.kind === 'column') {
                copyText(menu.payload.label);
              }
            },
          },
          {
            id: 'copy-column-id',
            label: '复制字段 ID',
            icon: <Copy size={14} />,
            disabled: menu?.payload?.kind !== 'column',
            onSelect: () => {
              if (menu?.payload?.kind === 'column') {
                copyText(menu.payload.columnId);
              }
            },
          },
        ]}
      />
    </Card>
  );
}
