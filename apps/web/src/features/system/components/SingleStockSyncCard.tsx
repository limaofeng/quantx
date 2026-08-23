import { ArrowRight, Database, Loader2, Search } from 'lucide-react';
import React, { useMemo, useState } from 'react';
import { useQuery } from 'urql';
import { useLocation } from 'wouter';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { gql } from '@/generated/gql';
import { useToast } from '@/hooks/use-toast';
import { financialToneClass } from '@/shared/utils/financialColors';
import { cn } from '@/utils/cn';

const SEARCH_DATA_PORTAL_INSTRUMENTS = gql(`
  query SearchDataPortalInstruments($searchQuery: String!, $limit: Int) {
    instruments(
      where: {
        stockCode_contains: $searchQuery
        type_in: [STOCK, ETF, INDEX]
      }
      limit: $limit
    ) {
      id
      name
      market
      type
      isTrading
      updatedAt
      quote {
        lastPrice
        changePercent
        time
      }
    }
  }
`);

type DataPortalInstrument = {
  id: string;
  name?: string | null;
  market?: string | null;
  type?: string | null;
  isTrading?: boolean | null;
  updatedAt?: string | null;
  quote?: {
    lastPrice?: number | null;
    changePercent?: number | null;
    time?: string | null;
  } | null;
};

export function StockDataQueryCard() {
  const { toast } = useToast();
  const [, setLocation] = useLocation();
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCode, setSelectedCode] = useState<string | null>(null);

  const normalizedQuery = searchQuery.trim().toUpperCase();
  const [{ data, fetching }] = useQuery({
    query: SEARCH_DATA_PORTAL_INSTRUMENTS,
    variables: { searchQuery: normalizedQuery, limit: 8 },
    pause: normalizedQuery.length < 2,
  });

  const instruments = useMemo<DataPortalInstrument[]>(
    () => (data?.instruments ?? []) as DataPortalInstrument[],
    [data?.instruments]
  );
  const selectedInstrument =
    instruments.find(item => item.id === selectedCode) ?? instruments[0];

  const handleViewDetails = () => {
    const target = selectedInstrument?.id || normalizedQuery;
    if (!target) {
      toast({
        title: '请输入股票/指数/ETF代码',
        variant: 'destructive',
      });
      return;
    }
    setLocation(`/settings/data/${target}`);
  };

  return (
    <div className="h-full flex flex-col rounded-xl border border-slate-200/40 dark:border-slate-800/40 bg-white/70 dark:bg-slate-900/40 backdrop-blur-sm overflow-hidden p-4">
      <div className="flex items-center gap-2 mb-3">
        <div className="p-1 rounded bg-indigo-500/10 text-indigo-600 dark:text-indigo-400">
          <Database className="w-3.5 h-3.5" />
        </div>
        <h3 className="text-sm font-bold text-slate-700 dark:text-slate-200">
          标的数据查询
        </h3>
      </div>

      <div className="flex gap-2 mb-3">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-500" />
          <Input
            placeholder="代码 (如 600519.SH)"
            className="pl-8 h-8 text-xs bg-white dark:bg-black/20 border-slate-200 dark:border-slate-800"
            value={searchQuery}
            onChange={e => {
              setSearchQuery(e.target.value);
              setSelectedCode(null);
            }}
            onKeyDown={e => e.key === 'Enter' && handleViewDetails()}
          />
        </div>
        <Button
          size="sm"
          onClick={handleViewDetails}
          disabled={fetching && !selectedInstrument}
          className="h-8 px-3 text-xs font-bold"
        >
          {fetching ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : '进入'}
        </Button>
      </div>

      <div className="flex-1 min-h-0">
        {normalizedQuery.length < 2 ? (
          <div className="h-full flex flex-col items-center justify-center text-slate-400 text-xs border-2 border-dashed border-slate-100 dark:border-slate-800/60 rounded-lg bg-slate-50/60 dark:bg-white/5">
            <Database className="w-8 h-8 mb-2 opacity-10" />
            <p className="opacity-70">请输入至少 2 位代码</p>
          </div>
        ) : instruments.length === 0 && !fetching ? (
          <div className="h-full flex flex-col items-center justify-center text-slate-400 text-xs border-2 border-dashed border-slate-100 dark:border-slate-800/60 rounded-lg bg-slate-50/60 dark:bg-white/5">
            <Search className="w-8 h-8 mb-2 opacity-10" />
            <p className="opacity-70">未找到匹配标的</p>
          </div>
        ) : (
          <div className="h-full overflow-y-auto custom-scrollbar space-y-2 pr-1">
            {instruments.map(item => {
              const active = selectedInstrument?.id === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  className={cn(
                    'w-full text-left rounded-lg border p-2.5 transition-colors cursor-pointer',
                    active
                      ? 'border-indigo-300 bg-indigo-50 text-slate-900 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-slate-100'
                      : 'border-slate-100 bg-white hover:border-slate-200 hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60 dark:hover:bg-white/5'
                  )}
                  onClick={() => setSelectedCode(item.id)}
                  onDoubleClick={() => setLocation(`/settings/data/${item.id}`)}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-black">
                        {item.name || item.id}
                      </div>
                      <div className="mt-1 flex items-center gap-1.5">
                        <Badge
                          variant="secondary"
                          className="h-4 rounded-sm px-1 text-[10px] font-mono"
                        >
                          {item.id}
                        </Badge>
                        <span className="text-[10px] text-slate-400">
                          {item.market || '--'} · {item.type || '--'}
                        </span>
                      </div>
                    </div>
                    <div className="text-right shrink-0">
                      <div className="font-mono text-xs font-bold">
                        {item.quote?.lastPrice?.toFixed(2) ?? '--'}
                      </div>
                      <div
                        className={cn(
                          'font-mono text-[10px]',
                          financialToneClass(item.quote?.changePercent)
                        )}
                      >
                        {item.quote?.changePercent == null
                          ? '--'
                          : `${item.quote.changePercent >= 0 ? '+' : ''}${item.quote.changePercent.toFixed(2)}%`}
                      </div>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      <Button
        onClick={handleViewDetails}
        size="sm"
        className="w-full mt-3 h-8 text-xs bg-indigo-600 hover:bg-indigo-700 text-white shadow-sm"
      >
        查看数据详情 <ArrowRight className="w-3 h-3 ml-1" />
      </Button>
    </div>
  );
}
