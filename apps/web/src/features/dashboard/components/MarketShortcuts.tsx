// A股行情快捷入口
import {
  BarChart3,
  CalendarDays,
  CandlestickChart,
  LineChart,
  Percent,
  Search,
  type LucideIcon,
} from 'lucide-react';
import { Link } from 'wouter';

import { Card } from '@/components/ui/card';

interface MarketShortcut {
  accentClass: string;
  href: string;
  icon: LucideIcon;
  label: string;
  meta: string;
  testId: string;
  value: string;
}

const marketShortcuts: MarketShortcut[] = [
  {
    accentClass:
      'border-red-500/20 bg-red-500/10 text-red-300 group-hover:border-red-400/50',
    href: '/settings/data/market',
    icon: BarChart3,
    label: '市场总览',
    meta: '覆盖率 98.5%',
    testId: 'market-shortcut-overview',
    value: 'A股 5,243',
  },
  {
    accentClass:
      'border-sky-500/20 bg-sky-500/10 text-sky-300 group-hover:border-sky-400/50',
    href: '/settings/data/market?tab=indices',
    icon: LineChart,
    label: '沪深指数',
    meta: '指数快照',
    testId: 'market-shortcut-indices',
    value: '上证 / 深成',
  },
  {
    accentClass:
      'border-emerald-500/20 bg-emerald-500/10 text-emerald-300 group-hover:border-emerald-400/50',
    href: '/settings/data/market?tab=stocks',
    icon: CandlestickChart,
    label: '个股数据',
    meta: '单股查询',
    testId: 'market-shortcut-stocks',
    value: '基础 + K线',
  },
  {
    accentClass:
      'border-amber-500/20 bg-amber-500/10 text-amber-300 group-hover:border-amber-400/50',
    href: '/settings/data/sectors',
    icon: LineChart,
    label: '板块数据',
    meta: '板块映射',
    testId: 'market-shortcut-sectors',
    value: '行业 / 概念',
  },
  {
    accentClass:
      'border-violet-500/20 bg-violet-500/10 text-violet-300 group-hover:border-violet-400/50',
    href: '/settings/data/market?tab=ex-rights',
    icon: Percent,
    label: '除权数据',
    meta: '复权因子',
    testId: 'market-shortcut-ex-rights',
    value: '分红 / 配股',
  },
  {
    accentClass:
      'border-cyan-500/20 bg-cyan-500/10 text-cyan-300 group-hover:border-cyan-400/50',
    href: '/settings/data/calendar',
    icon: CalendarDays,
    label: '交易日历',
    meta: '开休市',
    testId: 'market-shortcut-calendar',
    value: 'SH / SZ',
  },
  {
    accentClass:
      'border-fuchsia-500/20 bg-fuchsia-500/10 text-fuchsia-300 group-hover:border-fuchsia-400/50',
    href: '/screening',
    icon: Search,
    label: '股票筛选',
    meta: '结果表格',
    testId: 'market-shortcut-screening',
    value: '条件池',
  },
];

export function MarketShortcuts() {
  return (
    <Card className="rounded-lg border-white/10 bg-slate-900/70 p-ui-section">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="truncate text-ui-label font-black uppercase tracking-[0.2em] text-slate-300">
          行情快捷方式
        </h3>
        <span className="shrink-0 rounded-md border border-white/10 bg-white/[0.03] px-2 py-1 text-ui-caption font-black uppercase tracking-wider text-slate-500">
          A股数据
        </span>
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {marketShortcuts.map(item => {
          const Icon = item.icon;

          return (
            <Link
              key={item.label}
              href={item.href}
              aria-label={item.label}
              className="group min-h-[104px] rounded-lg border border-white/5 bg-white/[0.025] p-3 transition-colors hover:border-white/15 hover:bg-white/[0.045] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70"
              data-testid={item.testId}
            >
              <span className="flex h-full flex-col justify-between gap-3">
                <span className="flex items-start justify-between gap-3">
                  <span>
                    <span className="block text-ui-label font-black text-slate-100">
                      {item.label}
                    </span>
                    <span className="mt-1 block text-ui-caption font-bold uppercase tracking-wider text-slate-600">
                      {item.meta}
                    </span>
                  </span>
                  <span
                    className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border ${item.accentClass}`}
                  >
                    <Icon className="h-4 w-4" />
                  </span>
                </span>
                <span className="font-mono text-ui-body font-black text-slate-300">
                  {item.value}
                </span>
              </span>
            </Link>
          );
        })}
      </div>
    </Card>
  );
}
