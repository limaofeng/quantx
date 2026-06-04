import {
  Activity,
  AlertCircle,
  ArrowUpRight,
  BarChart3,
  BookOpen,
  Boxes,
  GitCommitHorizontal,
  History,
  LayoutDashboard,
  LineChart,
  PauseCircle,
  Plus,
  RefreshCw,
  Settings,
  ShieldCheck,
  TestTube,
  type LucideIcon,
} from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';
import { useQuery } from 'urql';
import { useLocation } from 'wouter';

import { TabBar, type StudioTab } from '@/components/studio-workbench';
import { cn } from '@/utils/cn';

import AvailableStrategies from '../components/AvailableStrategies';
import {
  StrategyStudioShell,
  type StrategyStudioMode,
} from '../components/StrategyStudioShell';
import {
  getStrategyRunState,
  mapStrategyInstanceView,
  normalizeStrategyRunMode,
  type StrategyInstance,
  type StrategyRunTone,
} from '../domain';
import { StrategyInstancesQuery } from '../hooks/strategyInstanceOperations';

const strategyTabs: StudioTab[] = [
  {
    id: 'runs',
    type: 'strategy-mode',
    name: '策略看板',
    icon: LayoutDashboard,
  },
  { id: 'catalog', type: 'strategy-mode', name: '策略库', icon: BookOpen },
];

const modeMeta: Record<
  StrategyStudioMode,
  {
    description: string;
    icon: LucideIcon;
    title: string;
  }
> = {
  BACKTEST: {
    description: '回测版本、区间与绩效快照。',
    icon: History,
    title: '回测版本',
  },
  CATALOG: {
    description: '从策略模板创建新的单标的 A 股策略实例。',
    icon: BookOpen,
    title: '策略库',
  },
  CONFIG: {
    description: '实例参数、网格账本和仓位归因。',
    icon: Settings,
    title: '参数配置',
  },
  MONITOR: {
    description: '图表、行情 tick 与策略事件。',
    icon: Activity,
    title: '图表监控',
  },
  RUNS: {
    description: '全实例运行态势、模式分布和最近活动。',
    icon: LayoutDashboard,
    title: '策略 Dashboard',
  },
  TRACE: {
    description: 'DecisionTrace、TradeIntent 和执行链路审计。',
    icon: GitCommitHorizontal,
    title: '决策追踪',
  },
};

const toneClasses: Record<
  StrategyRunTone,
  {
    border: string;
    dot: string;
    fill: string;
    text: string;
  }
> = {
  amber: {
    border: 'border-amber-400/25',
    dot: 'bg-amber-400',
    fill: 'bg-amber-500/10',
    text: 'text-amber-300',
  },
  blue: {
    border: 'border-blue-400/25',
    dot: 'bg-blue-400',
    fill: 'bg-blue-500/10',
    text: 'text-blue-300',
  },
  emerald: {
    border: 'border-emerald-400/25',
    dot: 'bg-emerald-400',
    fill: 'bg-emerald-500/10',
    text: 'text-emerald-300',
  },
  purple: {
    border: 'border-purple-400/25',
    dot: 'bg-purple-400',
    fill: 'bg-purple-500/10',
    text: 'text-purple-300',
  },
  rose: {
    border: 'border-rose-400/25',
    dot: 'bg-rose-400',
    fill: 'bg-rose-500/10',
    text: 'text-rose-300',
  },
  slate: {
    border: 'border-slate-500/25',
    dot: 'bg-slate-500',
    fill: 'bg-slate-500/10',
    text: 'text-slate-400',
  },
};

function getModeIcon(mode?: string | null) {
  const normalizedMode = normalizeStrategyRunMode(mode);
  if (normalizedMode === 'PAPER') return TestTube;
  if (normalizedMode === 'LIVE') return Activity;
  return LineChart;
}

function formatCompactTime(value?: string | null) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';

  return date.toLocaleString('zh-CN', {
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    month: '2-digit',
  });
}

function getActivityTimestamp(instance: StrategyInstance) {
  const value =
    instance.lastDecisionAt || instance.updatedAt || instance.createdAt;
  const timestamp = value ? new Date(value).getTime() : 0;
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function getInstanceDetailUrl(instance: StrategyInstance) {
  if (!instance.strategyId) return null;
  return `/strategies/${instance.strategyId}/runs/${encodeURIComponent(
    instance.id
  )}`;
}

function ModePlaceholder({
  mode,
  onCreate,
  onOpenRuns,
}: {
  mode: StrategyStudioMode;
  onCreate: () => void;
  onOpenRuns: () => void;
}) {
  const meta = modeMeta[mode];
  const Icon = meta.icon;

  return (
    <div className="flex h-full min-h-0 items-center justify-center bg-[#08101d] p-6">
      <div className="w-full max-w-md rounded-lg border border-dashed border-white/10 bg-white/[0.03] p-6 text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-lg border border-red-500/20 bg-red-500/10 text-red-300">
          <Icon className="h-5 w-5" />
        </div>
        <h2 className="mt-4 text-sm font-black uppercase tracking-[0.2em] text-slate-100">
          {meta.title}
        </h2>
        <p className="mt-3 text-xs leading-relaxed text-slate-500">
          {meta.description}
        </p>
        <div className="mt-5 flex justify-center gap-2">
          <button
            type="button"
            onClick={onOpenRuns}
            className="h-8 rounded-md border border-white/10 px-3 text-[10px] font-bold uppercase tracking-wider text-slate-300 transition-colors hover:border-red-500/40 hover:text-red-300"
          >
            查看实例
          </button>
          <button
            type="button"
            onClick={onCreate}
            className="h-8 rounded-md bg-red-500 px-3 text-[10px] font-black uppercase tracking-wider text-white transition-colors hover:bg-red-400"
          >
            新建实例
          </button>
        </div>
      </div>
    </div>
  );
}

function StrategyInstanceNavItem({
  instance,
  onOpen,
}: {
  instance: StrategyInstance;
  onOpen: (instance: StrategyInstance) => void;
}) {
  const state = getStrategyRunState(instance.mode, instance.status);
  const ModeIcon = getModeIcon(instance.mode);
  const tone = toneClasses[state.color];
  const detailUrl = getInstanceDetailUrl(instance);

  return (
    <button
      type="button"
      disabled={!detailUrl}
      onClick={() => onOpen(instance)}
      title={detailUrl ? instance.displayName : '缺少策略模板 ID，无法打开'}
      className={cn(
        'group flex w-full items-start gap-2.5 rounded-md border px-2.5 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70',
        detailUrl
          ? 'border-transparent text-slate-400 hover:border-white/10 hover:bg-white/[0.04] hover:text-slate-200'
          : 'cursor-not-allowed border-transparent text-slate-600 opacity-70'
      )}
    >
      <span
        className={cn(
          'mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border',
          tone.border,
          tone.fill,
          tone.text
        )}
      >
        <ModeIcon className="h-3.5 w-3.5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate text-xs font-bold">
            {instance.displayName}
          </span>
          <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', tone.dot)} />
        </span>
        <span className="mt-0.5 block truncate font-mono text-[10px] text-slate-500">
          {instance.instrumentCode}
        </span>
        <span className="mt-1 flex items-center justify-between gap-2 text-[10px] text-slate-600">
          <span>
            {state.modeLabel} / {state.statusLabel}
          </span>
          <span>{formatCompactTime(instance.lastDecisionAt)}</span>
        </span>
      </span>
    </button>
  );
}

function StrategySidebar({
  activeMode,
  error,
  fetching,
  instances,
  onCreate,
  onModeChange,
  onOpenInstance,
  onRefresh,
}: {
  activeMode: StrategyStudioMode;
  error?: { message: string } | null;
  fetching: boolean;
  instances: StrategyInstance[];
  onCreate: () => void;
  onModeChange: (mode: StrategyStudioMode) => void;
  onOpenInstance: (instance: StrategyInstance) => void;
  onRefresh: () => void;
}) {
  const activeCount = instances.filter(
    instance => getStrategyRunState(instance.mode, instance.status).isActive
  ).length;
  const primaryItems = [
    {
      id: 'RUNS' as const,
      icon: LayoutDashboard,
      label: '策略看板',
      meta: `${instances.length} 个实例`,
    },
    {
      id: 'CATALOG' as const,
      icon: BookOpen,
      label: '策略库',
      meta: 'Catalog',
    },
  ];

  return (
    <aside className="flex h-full min-h-0 flex-col">
      <div className="border-b border-white/5 px-4 py-3">
        <div className="text-[10px] font-black uppercase tracking-[0.24em] text-red-400">
          Strategy Studio
        </div>
        <div className="mt-1 text-xs font-medium leading-relaxed text-slate-500">
          策略实例、模板和运行状态集中管理。
        </div>
      </div>

      <div className="border-b border-white/5 p-2">
        <div className="mb-2 px-2 text-[10px] font-black uppercase tracking-[0.2em] text-slate-600">
          Workspace
        </div>
        <div className="space-y-1">
          {primaryItems.map(item => {
            const Icon = item.icon;
            const isActive = activeMode === item.id;

            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onModeChange(item.id)}
                className={cn(
                  'flex w-full items-center gap-3 rounded-md border px-2.5 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70',
                  isActive
                    ? 'border-red-500/30 bg-red-500/10 text-red-100'
                    : 'border-transparent text-slate-400 hover:border-white/10 hover:bg-white/[0.04] hover:text-slate-200'
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-bold">
                    {item.label}
                  </span>
                  <span className="block truncate text-[10px] font-medium text-slate-600">
                    {item.meta}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2 custom-scrollbar">
        <div className="mb-2 flex items-center justify-between px-2">
          <div>
            <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-600">
              Strategy Instances
            </div>
            <div className="mt-0.5 text-[10px] font-medium text-slate-600">
              {activeCount} 运行中 / {instances.length} 总实例
            </div>
          </div>
          <button
            type="button"
            onClick={onRefresh}
            className="flex h-7 w-7 items-center justify-center rounded-md border border-white/10 text-slate-500 transition-colors hover:border-red-500/40 hover:text-red-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70"
            title="刷新策略实例"
          >
            <RefreshCw
              className={cn('h-3.5 w-3.5', fetching && 'animate-spin')}
            />
          </button>
        </div>

        {error && (
          <div className="mb-2 rounded-md border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-[11px] font-medium leading-relaxed text-rose-300">
            {error.message}
          </div>
        )}

        {fetching && instances.length === 0 ? (
          <div className="space-y-2">
            {[1, 2, 3].map(item => (
              <div
                key={item}
                className="h-[74px] animate-pulse rounded-md border border-white/5 bg-white/[0.03]"
              />
            ))}
          </div>
        ) : instances.length === 0 ? (
          <div className="rounded-md border border-dashed border-white/10 px-3 py-5 text-center">
            <Boxes className="mx-auto h-5 w-5 text-slate-600" />
            <div className="mt-2 text-xs font-bold text-slate-400">
              暂无策略实例
            </div>
            <div className="mt-1 text-[10px] leading-relaxed text-slate-600">
              等待创建新的单标的策略实例。
            </div>
          </div>
        ) : (
          <div className="space-y-1">
            {instances.map(instance => (
              <StrategyInstanceNavItem
                key={instance.id}
                instance={instance}
                onOpen={onOpenInstance}
              />
            ))}
          </div>
        )}
      </div>

      <div className="border-t border-white/5 p-3">
        <button
          type="button"
          onClick={onCreate}
          className="flex h-8 w-full items-center justify-center gap-2 rounded-md bg-red-500 text-[10px] font-black uppercase tracking-wider text-white transition-colors hover:bg-red-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300"
        >
          <Plus className="h-3.5 w-3.5" />
          新建策略实例
        </button>
      </div>
    </aside>
  );
}

function StrategyDashboardHome({
  error,
  fetching,
  instances,
  onCreate,
  onOpenCatalog,
  onOpenInstance,
  onRefresh,
}: {
  error?: { message: string } | null;
  fetching: boolean;
  instances: StrategyInstance[];
  onCreate: () => void;
  onOpenCatalog: () => void;
  onOpenInstance: (instance: StrategyInstance) => void;
  onRefresh: () => void;
}) {
  const modeCounts = useMemo(
    () => ({
      BACKTEST: instances.filter(
        instance => normalizeStrategyRunMode(instance.mode) === 'BACKTEST'
      ).length,
      LIVE: instances.filter(
        instance => normalizeStrategyRunMode(instance.mode) === 'LIVE'
      ).length,
      PAPER: instances.filter(
        instance => normalizeStrategyRunMode(instance.mode) === 'PAPER'
      ).length,
    }),
    [instances]
  );
  const activeInstances = instances.filter(
    instance => getStrategyRunState(instance.mode, instance.status).isActive
  );
  const pausedInstances = instances.filter(
    instance =>
      getStrategyRunState(instance.mode, instance.status).status === 'PAUSED'
  );
  const terminalInstances = instances.filter(
    instance => getStrategyRunState(instance.mode, instance.status).isTerminal
  );
  const latestInstance = instances[0];
  const openableInstances = instances.filter(getInstanceDetailUrl).slice(0, 5);

  const kpis: Array<{
    icon: LucideIcon;
    label: string;
    tone: StrategyRunTone;
    value: string | number;
  }> = [
    {
      icon: Boxes,
      label: '实例总数',
      tone: 'blue',
      value: instances.length,
    },
    {
      icon: Activity,
      label: '运行中',
      tone: 'emerald',
      value: activeInstances.length,
    },
    {
      icon: PauseCircle,
      label: '暂停中',
      tone: 'amber',
      value: pausedInstances.length,
    },
    {
      icon: ShieldCheck,
      label: '已归档',
      tone: 'slate',
      value: terminalInstances.length,
    },
  ];
  const modeRows = [
    {
      icon: LineChart,
      label: '回测',
      tone: 'blue' as StrategyRunTone,
      value: modeCounts.BACKTEST,
    },
    {
      icon: TestTube,
      label: '模拟',
      tone: 'emerald' as StrategyRunTone,
      value: modeCounts.PAPER,
    },
    {
      icon: Activity,
      label: '实盘',
      tone: 'rose' as StrategyRunTone,
      value: modeCounts.LIVE,
    },
  ];

  return (
    <div className="h-full overflow-y-auto bg-[#08101d] p-4 custom-scrollbar">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-4">
        <section className="rounded-lg border border-white/5 bg-[#0b1120]/70 px-4 py-4">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-red-400" />
                <span className="text-[10px] font-black uppercase tracking-[0.24em] text-red-300">
                  Strategy Dashboard
                </span>
              </div>
              <h1 className="mt-2 text-xl font-black tracking-tight text-slate-50">
                策略运行态势
              </h1>
              <p className="mt-1 max-w-2xl text-xs leading-relaxed text-slate-500">
                全实例运行状态、模式分布与近期决策活动。
              </p>
            </div>
            <div className="flex shrink-0 flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={onRefresh}
                className="flex h-8 items-center gap-2 rounded-md border border-white/10 px-3 text-[10px] font-black uppercase tracking-wider text-slate-300 transition-colors hover:border-red-500/40 hover:text-red-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70"
              >
                <RefreshCw
                  className={cn('h-3.5 w-3.5', fetching && 'animate-spin')}
                />
                同步
              </button>
              <button
                type="button"
                onClick={onOpenCatalog}
                className="flex h-8 items-center gap-2 rounded-md border border-white/10 px-3 text-[10px] font-black uppercase tracking-wider text-slate-300 transition-colors hover:border-red-500/40 hover:text-red-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70"
              >
                <BookOpen className="h-3.5 w-3.5" />
                策略库
              </button>
              <button
                type="button"
                onClick={onCreate}
                className="flex h-8 items-center gap-2 rounded-md bg-red-500 px-3 text-[10px] font-black uppercase tracking-wider text-white transition-colors hover:bg-red-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300"
              >
                <Plus className="h-3.5 w-3.5" />
                新建实例
              </button>
            </div>
          </div>
        </section>

        {error && (
          <div className="rounded-lg border border-rose-500/20 bg-rose-500/10 px-4 py-3 text-xs font-bold text-rose-300">
            <AlertCircle className="mr-2 inline h-4 w-4 align-[-3px]" />
            {error.message}
          </div>
        )}

        <section className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          {kpis.map(kpi => {
            const Icon = kpi.icon;
            const tone = toneClasses[kpi.tone];

            return (
              <div
                key={kpi.label}
                className="rounded-lg border border-white/5 bg-[#0b1120]/70 p-3"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-600">
                    {kpi.label}
                  </span>
                  <span
                    className={cn(
                      'flex h-7 w-7 items-center justify-center rounded-md border',
                      tone.border,
                      tone.fill,
                      tone.text
                    )}
                  >
                    <Icon className="h-3.5 w-3.5" />
                  </span>
                </div>
                <div className="mt-4 font-mono text-2xl font-bold tabular-nums text-slate-50">
                  {fetching && instances.length === 0 ? '--' : kpi.value}
                </div>
              </div>
            );
          })}
        </section>

        <section className="grid min-h-[360px] grid-cols-1 gap-3 xl:grid-cols-[1.45fr_0.9fr]">
          <div className="rounded-lg border border-white/5 bg-[#0b1120]/70 p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-black text-slate-100">
                  最近活动
                </h2>
                <p className="mt-1 text-[11px] text-slate-600">
                  按最近更新时间展示可打开的策略实例。
                </p>
              </div>
              {latestInstance && (
                <div className="hidden text-right text-[10px] text-slate-600 sm:block">
                  最近更新
                  <div className="font-mono text-slate-400">
                    {formatCompactTime(
                      latestInstance.lastDecisionAt || latestInstance.updatedAt
                    )}
                  </div>
                </div>
              )}
            </div>

            {fetching && instances.length === 0 ? (
              <div className="space-y-2">
                {[1, 2, 3, 4].map(item => (
                  <div
                    key={item}
                    className="h-14 animate-pulse rounded-md border border-white/5 bg-white/[0.03]"
                  />
                ))}
              </div>
            ) : openableInstances.length === 0 ? (
              <div className="flex min-h-[220px] items-center justify-center rounded-lg border border-dashed border-white/10 text-center">
                <div>
                  <BarChart3 className="mx-auto h-8 w-8 text-slate-600" />
                  <div className="mt-3 text-sm font-bold text-slate-300">
                    还没有可打开的策略 dashboard
                  </div>
                  <button
                    type="button"
                    onClick={onCreate}
                    className="mt-4 h-8 rounded-md bg-red-500 px-3 text-[10px] font-black uppercase tracking-wider text-white transition-colors hover:bg-red-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300"
                  >
                    新建策略实例
                  </button>
                </div>
              </div>
            ) : (
              <div className="divide-y divide-white/5 overflow-hidden rounded-lg border border-white/5">
                {openableInstances.map(instance => {
                  const state = getStrategyRunState(
                    instance.mode,
                    instance.status
                  );
                  const tone = toneClasses[state.color];
                  const ModeIcon = getModeIcon(instance.mode);

                  return (
                    <button
                      key={instance.id}
                      type="button"
                      onClick={() => onOpenInstance(instance)}
                      className="group flex w-full items-center gap-3 bg-[#08101d]/50 px-3 py-3 text-left transition-colors hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70"
                    >
                      <span
                        className={cn(
                          'flex h-9 w-9 shrink-0 items-center justify-center rounded-md border',
                          tone.border,
                          tone.fill,
                          tone.text
                        )}
                      >
                        <ModeIcon className="h-4 w-4" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-bold text-slate-200">
                          {instance.displayName}
                        </span>
                        <span className="mt-0.5 block truncate font-mono text-[10px] text-slate-600">
                          {instance.instrumentCode} / {instance.id}
                        </span>
                      </span>
                      <span className="hidden shrink-0 text-right sm:block">
                        <span
                          className={cn('block text-xs font-bold', tone.text)}
                        >
                          {state.statusLabel}
                        </span>
                        <span className="mt-0.5 block text-[10px] text-slate-600">
                          {formatCompactTime(
                            instance.lastDecisionAt || instance.updatedAt
                          )}
                        </span>
                      </span>
                      <ArrowUpRight className="h-4 w-4 shrink-0 text-slate-600 transition-colors group-hover:text-red-300" />
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 gap-3">
            <div className="rounded-lg border border-white/5 bg-[#0b1120]/70 p-4">
              <h2 className="text-sm font-black text-slate-100">模式分布</h2>
              <div className="mt-3 space-y-2">
                {modeRows.map(row => {
                  const Icon = row.icon;
                  const tone = toneClasses[row.tone];
                  const width =
                    instances.length > 0
                      ? `${Math.max(8, (row.value / instances.length) * 100)}%`
                      : '0%';

                  return (
                    <div key={row.label} className="space-y-1.5">
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2 text-xs font-bold text-slate-300">
                          <Icon className={cn('h-3.5 w-3.5', tone.text)} />
                          {row.label}
                        </div>
                        <span className="font-mono text-xs text-slate-400">
                          {row.value}
                        </span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.05]">
                        <div
                          className={cn('h-full rounded-full', tone.dot)}
                          style={{ width }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="rounded-lg border border-white/5 bg-[#0b1120]/70 p-4">
              <h2 className="text-sm font-black text-slate-100">执行边界</h2>
              <div className="mt-3 space-y-2 text-[11px] leading-relaxed text-slate-500">
                <div className="rounded-md border border-white/5 bg-white/[0.03] px-3 py-2">
                  策略输出保持为 TradeIntent 与状态补丁。
                </div>
                <div className="rounded-md border border-white/5 bg-white/[0.03] px-3 py-2">
                  成交真源保持来自 miniQMT 委托与成交回报。
                </div>
                <div className="rounded-md border border-white/5 bg-white/[0.03] px-3 py-2">
                  A 股规则、仓位归因与风控在交易域处理。
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

export default function StrategiesPage() {
  const [, setLocation] = useLocation();
  const [activeMode, setActiveMode] = useState<StrategyStudioMode>('RUNS');
  const [{ data, fetching, error }, reexecuteQuery] = useQuery({
    query: StrategyInstancesQuery,
    requestPolicy: 'cache-and-network',
  });
  const instances: StrategyInstance[] = useMemo(
    () =>
      ((((data as any)?.strategyInstances || []) as unknown[])
        .map(mapStrategyInstanceView)
        .sort((a, b) => {
          const activeDelta =
            Number(getStrategyRunState(b.mode, b.status).isActive) -
            Number(getStrategyRunState(a.mode, a.status).isActive);
          if (activeDelta !== 0) return activeDelta;
          return getActivityTimestamp(b) - getActivityTimestamp(a);
        })),
    [data]
  );
  const refreshInstances = useCallback(
    () => reexecuteQuery({ requestPolicy: 'network-only' }),
    [reexecuteQuery]
  );
  const openInstance = useCallback((instance: StrategyInstance) => {
    const detailUrl = getInstanceDetailUrl(instance);
    if (!detailUrl) return;
    setLocation(detailUrl);
  }, [setLocation]);

  const activeTabId = activeMode === 'CATALOG' ? 'catalog' : 'runs';
  const activeMeta = modeMeta[activeMode];
  const ActiveIcon = activeMeta.icon;
  const content = useMemo(() => {
    if (activeMode === 'RUNS') {
      return (
        <StrategyDashboardHome
          error={error}
          fetching={fetching}
          instances={instances}
          onCreate={() => setLocation('/strategies/run')}
          onOpenCatalog={() => setActiveMode('CATALOG')}
          onOpenInstance={openInstance}
          onRefresh={refreshInstances}
        />
      );
    }

    if (activeMode === 'CATALOG') {
      return (
        <div className="h-full overflow-y-auto bg-[#08101d] p-4 custom-scrollbar">
          <AvailableStrategies compact />
        </div>
      );
    }

    return (
      <ModePlaceholder
        mode={activeMode}
        onCreate={() => setLocation('/strategies/run')}
        onOpenRuns={() => setActiveMode('RUNS')}
      />
    );
  }, [
    activeMode,
    error,
    fetching,
    instances,
    openInstance,
    refreshInstances,
    setLocation,
  ]);

  return (
    <StrategyStudioShell
      activeMode={activeMode}
      className="h-full min-h-0"
      content={content}
      onModeChange={setActiveMode}
      sidebar={
        <StrategySidebar
          activeMode={activeMode}
          error={error}
          fetching={fetching}
          instances={instances}
          onCreate={() => setLocation('/strategies/run')}
          onModeChange={setActiveMode}
          onOpenInstance={openInstance}
          onRefresh={refreshInstances}
        />
      }
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            策略服务
          </span>
          <span className="text-slate-700">|</span>
          <span>{instances.length} 实例</span>
        </>
      }
      statusBarRight={
        <>
          <span className="inline-flex items-center gap-2">
            <ActiveIcon className="h-3 w-3 text-red-400" />
            {activeMeta.description}
          </span>
        </>
      }
      tabBar={
        <div className="flex h-10 shrink-0 items-center border-b border-white/5 bg-[#0b1120]/80">
          <div className="min-w-0 flex-1">
            <TabBar
              activeTabId={activeTabId}
              closable={false}
              onTabChange={tabId =>
                setActiveMode(tabId === 'catalog' ? 'CATALOG' : 'RUNS')
              }
              onTabClose={() => undefined}
              tabs={strategyTabs}
              themeColor="red"
            />
          </div>
        </div>
      }
    />
  );
}
