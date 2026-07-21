import {
  ArrowLeft,
  CandlestickChart,
  Database,
  FileText,
  RefreshCw,
} from 'lucide-react';
import { useLocation } from 'wouter';

import { Button } from '@/components/ui/button';

import { DataStudioPageFrame } from '../components/DataStudioPageFrame';
import { StockDataQueryCard } from '../components/SingleStockSyncCard';

const stockDataScopes = [
  {
    description: '代码、市场、交易状态与基础价格约束',
    icon: Database,
    label: '基础资料',
  },
  {
    description: '日线与分钟线的覆盖、缺口和样本窗口',
    icon: CandlestickChart,
    label: 'K线缓存',
  },
  {
    description: '利润、资产负债、现金流与股本记录',
    icon: FileText,
    label: '财务四表',
  },
  {
    description: 'Prefect 任务、最近运行与手动补拉',
    icon: RefreshCw,
    label: '同步任务',
  },
];

export function StockDataIndexPage() {
  const [, setLocation] = useLocation();

  return (
    <DataStudioPageFrame
      activeMode="STOCKS"
      description="单票数据覆盖、缺口与同步入口"
      title="个股数据"
    >
      <div className="container mx-auto flex max-w-[1280px] flex-col gap-4 pb-8">
        <div className="flex flex-col gap-3 py-1 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex min-w-0 items-center gap-3">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 shrink-0 rounded-lg border border-slate-200/60 bg-white/50 shadow-sm backdrop-blur-sm transition-colors hover:bg-white dark:border-white/5 dark:bg-white/5 dark:hover:bg-white/10"
              onClick={() => setLocation('/settings/data')}
            >
              <ArrowLeft className="h-4 w-4 text-slate-600 dark:text-slate-400" />
            </Button>
            <div className="min-w-0">
              <h1 className="truncate text-lg font-black leading-none tracking-tight text-slate-900 dark:text-white">
                个股数据
              </h1>
              <p className="mt-1 text-[10px] font-bold uppercase tracking-widest text-slate-400">
                stock data coverage · kline gaps · manual sync
              </p>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[420px_minmax(0,1fr)]">
          <div className="h-[420px]">
            <StockDataQueryCard />
          </div>

          <section className="overflow-hidden rounded-xl border border-slate-200/70 bg-white shadow-sm dark:border-slate-800/70 dark:bg-slate-950">
            <div className="border-b border-slate-100 bg-slate-50/60 px-5 py-4 dark:border-slate-800 dark:bg-white/[0.02]">
              <h2 className="text-base font-black text-slate-900 dark:text-white">
                数据检查范围
              </h2>
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                进入标的后查看每类数据是否存在、K线区间是否有缺口，并按区间补拉。
              </p>
            </div>
            <div className="grid gap-3 p-5 md:grid-cols-2">
              {stockDataScopes.map(scope => {
                const Icon = scope.icon;
                return (
                  <div
                    key={scope.label}
                    className="rounded-xl border border-slate-200 bg-slate-50/70 p-4 dark:border-slate-800 dark:bg-white/[0.02]"
                  >
                    <div className="flex items-center gap-2 text-sm font-black text-slate-900 dark:text-white">
                      <Icon className="h-4 w-4 text-indigo-500" />
                      {scope.label}
                    </div>
                    <p className="mt-2 text-xs leading-relaxed text-slate-500 dark:text-slate-400">
                      {scope.description}
                    </p>
                  </div>
                );
              })}
            </div>
          </section>
        </div>
      </div>
    </DataStudioPageFrame>
  );
}
