import { TrendingUp, Layers, Zap, BarChart3 } from 'lucide-react';
import React from 'react';

import { DynamicIndexCard } from './DynamicIndexCard';
import { IndexDataCard } from './IndexDataCard';

// Mock Data for UI/UX Pro Max Showcase
const MARKET_CORE = [
  {
    name: '上证指数',
    code: '000001.SH',
    price: '3,088.12',
    change: '+38.12',
    changePercent: '+1.25%',
    status: 'normal',
  },
  {
    name: '深证成指',
    code: '399001.SZ',
    price: '9,450.33',
    change: '-12.45',
    changePercent: '-0.13%',
    status: 'normal',
  },
  {
    name: '沪深300',
    code: '000300.SH',
    price: '3,560.88',
    change: '+45.22',
    changePercent: '+1.29%',
    status: 'normal',
  },
  {
    name: '中证500',
    code: '000905.SH',
    price: '5,120.45',
    change: '+20.11',
    changePercent: '+0.39%',
    status: 'normal',
  },
];

const GROWTH_TECH = [
  {
    name: '创业板指',
    code: '399006.SZ',
    price: '1,850.45',
    change: '+28.90',
    changePercent: '+1.58%',
    status: 'normal',
  },
  {
    name: '科创50',
    code: '000688.SH',
    price: '780.11',
    change: '+15.20',
    changePercent: '+1.98%',
    status: 'normal',
  },
  {
    name: '北证50',
    code: '899050.BJ',
    price: '890.33',
    change: '-5.67',
    changePercent: '-0.63%',
    status: 'warning',
  },
];

const KEY_SECTORS = [
  {
    name: '证券公司',
    code: '399975.SZ',
    price: '850.22',
    change: '+4.55',
    changePercent: '+0.54%',
    status: 'normal',
  },
  {
    name: '中证白酒',
    code: '399997.SZ',
    price: '12,450.00',
    change: '-120.00',
    changePercent: '-0.95%',
    status: 'normal',
  },
  {
    name: '新能源车',
    code: '399976.SZ',
    price: '4,560.11',
    change: '+89.22',
    changePercent: '+1.99%',
    status: 'normal',
  },
];

const SectionHeader = ({
  title,
  subtitle,
  icon: Icon,
}: {
  title: string;
  subtitle: string;
  icon: any;
}) => (
  <div className="flex items-center gap-3 mb-6 mt-8 first:mt-2 px-2">
    <div className="p-2 rounded-xl bg-indigo-500/10 text-indigo-500 ring-4 ring-indigo-500/5">
      <Icon className="w-5 h-5" />
    </div>
    <div className="flex items-baseline gap-2">
      <h3 className="text-base font-bold text-slate-800 dark:text-slate-100 leading-none">
        {title}
      </h3>
      <p className="text-[9px] font-black uppercase tracking-[0.15em] text-slate-400">
        {subtitle}
      </p>
    </div>
  </div>
);

export function ChinaIndicesManager() {
  return (
    <div className="h-full flex flex-col overflow-hidden relative">
      {/* Decorative Background Elements */}
      <div className="absolute top-0 right-0 w-[500px] h-[500px] bg-gradient-to-br from-indigo-500/5 via-purple-500/5 to-transparent rounded-full blur-3xl pointer-events-none" />

      {/* Header Area */}
      <div className="shrink-0 p-1 pb-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-black text-slate-900 dark:text-white tracking-tight flex items-center gap-2">
              <TrendingUp className="w-5 h-5 text-indigo-500" />
              沪深市场核心指数
            </h2>
            <p className="text-sm text-slate-500 dark:text-slate-400 mt-1 max-w-2xl">
              实时监控A股市场核心板块与风格指数表现 (Real-time Market Indices)
            </p>
          </div>

          {/* Market Status Pulse */}
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-500/5 border border-emerald-500/20">
            <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-xs font-bold text-emerald-600 dark:text-emerald-400">
              市场交易中
            </span>
          </div>
        </div>
      </div>

      {/* Content Scroll Area */}
      <div className="flex-1 overflow-y-auto px-1 pb-6 scrollbar-thin scrollbar-thumb-slate-200 dark:scrollbar-thumb-white/10">
        <div className="max-w-7xl mx-auto space-y-2">
          {/* Section 1: Core Indices */}
          <SectionHeader
            title="核心宽基指数"
            subtitle="Market Core Indices"
            icon={Layers}
          />
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {MARKET_CORE.map((idx, i) => (
              <IndexDataCard key={i} {...idx} status={idx.status as any} />
            ))}
          </div>

          {/* Section 2: Growth & Tech */}
          <SectionHeader
            title="成长与科创"
            subtitle="Growth & Technology"
            icon={Zap}
          />
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {GROWTH_TECH.map((idx, i) => (
              <IndexDataCard key={i} {...idx} status={idx.status as any} />
            ))}
          </div>

          {/* Section 3: Thematic Sectors */}
          <SectionHeader
            title="重点行业主题"
            subtitle="Key Sectors & Themes"
            icon={BarChart3}
          />
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {KEY_SECTORS.map((idx, i) => (
              <IndexDataCard key={i} {...idx} status={idx.status as any} />
            ))}
            <DynamicIndexCard />
          </div>
        </div>

        <div className="flex justify-center items-center gap-1 mt-8 mb-4 opacity-50">
          <div className="w-1.5 h-1.5 rounded-full bg-indigo-500" />
          <div className="w-1.5 h-1.5 rounded-full bg-slate-200 dark:bg-white/20" />
          <div className="w-1.5 h-1.5 rounded-full bg-slate-200 dark:bg-white/20" />
        </div>
      </div>
    </div>
  );
}
