// A股行情快捷方式独立页面
import {
  Activity,
  ArrowLeft,
  BarChart3,
  Clock3,
  Database,
  Radio,
} from 'lucide-react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';

import { DashboardStudioShell } from '../components/DashboardStudioShell';
import { MarketShortcuts } from '../components/MarketShortcuts';

const marketSignals = [
  { label: '交易所', value: 'SH / SZ', meta: 'A股主市场', icon: Database },
  { label: '行情状态', value: '交易中', meta: '实时快照', icon: Radio },
  { label: '数据延迟', value: '45ms', meta: '本地服务', icon: Activity },
  { label: '交易日', value: 'T 日', meta: '日历校验', icon: Clock3 },
];

export default function MarketShortcutsPage() {
  return (
    <DashboardStudioShell
      content={
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="flex h-12 shrink-0 items-center justify-between border-b border-white/5 bg-[#0b1120]/70 px-4">
            <div className="flex min-w-0 items-center gap-3">
              <Link href="/">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 rounded-lg border border-white/10 bg-white/[0.03] text-slate-400 hover:border-red-500/40 hover:text-red-300"
                  aria-label="返回仪表板"
                >
                  <ArrowLeft className="h-4 w-4" />
                </Button>
              </Link>
              <div className="min-w-0">
                <div className="truncate text-xs font-black uppercase tracking-[0.2em] text-slate-200">
                  行情快捷方式
                </div>
                <div className="truncate text-[10px] font-medium text-slate-600">
                  A股市场数据、指数、个股、板块和交易日历入口
                </div>
              </div>
            </div>
            <div className="hidden items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-slate-500 md:flex">
              <span className="inline-flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                A股数据
              </span>
              <span className="text-slate-700">|</span>
              <span>Market Hub</span>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar">
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
              <div className="min-w-0 space-y-3">
                <section className="rounded-lg border border-white/10 bg-[#0f172a]/70 p-4">
                  <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
                    <div className="min-w-0">
                      <div className="mb-2 inline-flex h-8 items-center gap-2 rounded-lg border border-red-500/20 bg-red-500/10 px-3 text-xs font-black text-red-200">
                        <BarChart3 className="h-4 w-4" />
                        A股行情工作台
                      </div>
                      <h1 className="text-2xl font-black tracking-normal text-slate-100">
                        主要行情数据入口
                      </h1>
                      <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
                        聚合全市场、沪深指数、个股查询、板块数据、除权数据和交易日历，便于从仪表盘快速进入常用
                        A 股数据视图。
                      </p>
                    </div>
                    <Link href="/settings/data/market">
                      <Button
                        className="h-9 shrink-0 text-xs"
                        data-testid="market-shortcuts-open-market"
                      >
                        <BarChart3 className="h-4 w-4" />
                        打开市场数据
                      </Button>
                    </Link>
                  </div>
                </section>

                <MarketShortcuts />
              </div>

              <aside className="space-y-3">
                <section className="rounded-lg border border-white/10 bg-[#0f172a]/70 p-4">
                  <h2 className="mb-3 text-xs font-black uppercase tracking-[0.2em] text-slate-300">
                    数据状态
                  </h2>
                  <div className="space-y-2">
                    {marketSignals.map(item => {
                      const Icon = item.icon;

                      return (
                        <div
                          key={item.label}
                          className="flex items-center justify-between rounded-md border border-white/5 bg-white/[0.025] px-3 py-2"
                        >
                          <div className="flex min-w-0 items-center gap-3">
                            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-white/10 bg-white/[0.03] text-slate-400">
                              <Icon className="h-4 w-4" />
                            </span>
                            <span className="min-w-0">
                              <span className="block truncate text-xs font-bold text-slate-200">
                                {item.label}
                              </span>
                              <span className="block truncate text-[10px] font-medium text-slate-600">
                                {item.meta}
                              </span>
                            </span>
                          </div>
                          <span className="shrink-0 font-mono text-xs font-black text-slate-300">
                            {item.value}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </section>
              </aside>
            </div>
          </div>
        </div>
      }
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            行情快捷方式
          </span>
          <span className="text-slate-700">|</span>
          <span>A股数据入口</span>
        </>
      }
      statusBarRight={
        <>
          <span>全市场 / 指数 / 个股 / 板块</span>
          <span className="text-slate-700">|</span>
          <span>交易日历</span>
        </>
      }
    />
  );
}
