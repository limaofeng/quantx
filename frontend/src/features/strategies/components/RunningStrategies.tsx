import {
  AlertCircle,
  RefreshCw,
  Activity,
  ArrowUpRight,
  Plus,
} from 'lucide-react';
import { useState, useMemo } from 'react';
import { useQuery, useMutation } from 'urql';
import { useLocation } from 'wouter';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  type StrategyRunMode,
  type StrategyRunStatus,
} from '@/generated/gql/graphql';
import { cn } from '@/utils/cn';

import {
  getStrategyRunState,
  mapStrategyInstanceView,
  type StrategyInstance,
} from '../domain';
import {
  DeleteStrategyRunMutation,
  StrategyInstancesQuery,
} from '../hooks/strategyInstanceOperations';

import { ProfessionalBackground } from './ProfessionalBackground';
import StrategyInstanceCard from './StrategyInstanceCard';

interface RunningStrategiesProps {
  compact?: boolean;
}

export default function RunningStrategies({
  compact = false,
}: RunningStrategiesProps) {
  const [, setLocation] = useLocation();
  const [viewMode, setViewMode] = useState<'grid' | 'table'>('grid');
  const [showDemo, setShowDemo] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<{
    open: boolean;
    runId: string | null;
  }>({ open: false, runId: null });
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const [{ data, fetching, error }, reexecuteQuery] = useQuery({
    query: StrategyInstancesQuery,
    requestPolicy: 'cache-and-network',
  });

  const [{ fetching: deletingRun }, deleteStrategyRun] = useMutation(
    DeleteStrategyRunMutation
  );

  const instances: StrategyInstance[] = (
    ((data as any)?.strategyInstances || []) as unknown[]
  ).map(mapStrategyInstanceView);
  const runs = instances.map(instance => ({
    id: instance.id,
    name: instance.displayName,
    strategy: {
      id: instance.strategyId || 0,
      name: instance.strategyKey,
    },
    instruments: [instance.instrumentCode],
    mode: instance.mode as StrategyRunMode,
    status: instance.status as StrategyRunStatus,
    profitLoss: 0,
    totalTrades: 0,
    metrics: {},
    startTime: instance.createdAt,
  }));
  const runsWithInstances = useMemo(
    () => runs.map((run, index) => ({ run, instance: instances[index] })),
    [runs, instances]
  );

  const handleDeleteRequest = (runId: string) => {
    if (runId.startsWith('mock-')) {
      alert('演示数据不支持实际操作');
      return;
    }
    const target = runs.find(run => run.id === runId);
    if (!target || !getStrategyRunState(target.mode, target.status).canDelete) {
      setDeleteError('仅已停止、已完成或异常的策略实例可以删除。');
      return;
    }
    setDeleteError(null);
    setDeleteConfirm({ open: true, runId });
  };

  const handleDeleteConfirm = async () => {
    if (!deleteConfirm.runId) return;
    setDeleteError(null);
    const result = await deleteStrategyRun({ runId: deleteConfirm.runId });
    if (result.error) {
      setDeleteError(result.error.message);
      return;
    }

    const response = (result.data as any)?.deleteStrategyRun;
    if (!response?.success) {
      setDeleteError(response?.message || '删除失败，请稍后重试。');
      return;
    }

    reexecuteQuery({ requestPolicy: 'network-only' });
    setDeleteConfirm({ open: false, runId: null });
  };

  if (fetching)
    return (
      <div
        className={cn(
          'text-center font-mono text-[10px] uppercase tracking-widest text-muted-foreground animate-pulse',
          compact ? 'p-4' : 'p-8'
        )}
      >
        正在同步策略引擎...
      </div>
    );
  if (error)
    return (
      <Card
        className={cn(
          'border-rose-500/20 bg-rose-500/5 text-center',
          compact ? 'rounded-lg p-4' : 'rounded-[2rem] p-8'
        )}
      >
        <AlertCircle className="mx-auto h-8 w-8 mb-4 text-rose-500 opacity-50" />
        <p className="text-rose-500 font-black text-[10px] uppercase tracking-widest">
          策略服务连接异常: {error.message}
        </p>
      </Card>
    );

  const activeRuns = runs.filter(
    r => getStrategyRunState(r.mode, r.status).status === 'RUNNING'
  );
  const totalProfit = runs.reduce((acc, r) => acc + r.profitLoss, 0);
  const totalIntentCount = runs.reduce((acc, r) => {
    const metrics = (r.metrics || {}) as Record<string, unknown>;
    const intentCount =
      metrics.intentCount ||
      metrics.tradeIntentCount ||
      metrics.trade_intent_count ||
      metrics.dailyIntentCount ||
      0;
    return acc + (typeof intentCount === 'number' ? intentCount : 0);
  }, 0);

  return (
    <div className={cn('relative', compact ? 'space-y-4' : 'space-y-10')}>
      {/* Premium Dashboard Header */}
      {/* Premium Dashboard Header */}
      {!compact && (
        <div className="relative overflow-hidden bg-[#0F1729] border border-white/5 rounded-[2rem] p-8 shadow-2xl">
          <ProfessionalBackground />

          <div className="relative z-10 flex flex-col lg:flex-row lg:items-center justify-between gap-8">
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <div className="w-1.5 h-1.5 rounded-full bg-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.5)]" />
                <h2 className="text-[10px] font-bold uppercase tracking-[0.2em] text-blue-400">
                  策略控制中心
                </h2>
              </div>
              <h1 className="text-3xl font-black text-white tracking-tight leading-none italic uppercase">
                策略<span className="text-slate-500 not-italic">控制中心</span>
              </h1>
              <p className="text-slate-400 text-xs max-w-sm font-medium leading-relaxed border-l-2 border-slate-800 pl-3">
                统一管理策略实例、决策审计与执行状态，策略意图不再等同于委托或成交。
              </p>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-8 gap-y-6">
              <div className="space-y-0.5">
                <span className="text-[9px] font-bold text-slate-500 uppercase tracking-widest block">
                  实例总数
                </span>
                <p className="text-xl font-mono font-medium text-white tabular-nums tracking-tight">
                  {runs.length}
                </p>
              </div>
              <div className="space-y-0.5">
                <span className="text-[9px] font-bold text-emerald-500/70 uppercase tracking-widest block">
                  运行中
                </span>
                <p className="text-xl font-mono font-medium text-emerald-400 tabular-nums tracking-tight">
                  {activeRuns.length}
                </p>
              </div>
              <div className="space-y-0.5">
                <span className="text-[9px] font-bold text-blue-400/70 uppercase tracking-widest block">
                  策略意图
                </span>
                <p className="text-xl font-mono font-medium text-white tabular-nums tracking-tight">
                  {totalIntentCount || '--'}
                </p>
              </div>
              <div className="space-y-0.5">
                <span
                  className={`text-[9px] font-bold uppercase tracking-widest block ${totalProfit >= 0 ? 'text-emerald-500/70' : 'text-rose-500/70'}`}
                >
                  累计盈亏
                </span>
                <p
                  className={`text-xl font-mono font-medium tabular-nums tracking-tight ${totalProfit >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}
                >
                  {totalProfit >= 0 ? '+' : ''}
                  {totalProfit.toFixed(1)}
                </p>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className={cn(compact ? 'space-y-4' : 'space-y-8')}>
        <div
          className={cn(
            'flex items-center justify-between',
            compact ? 'px-0' : 'px-4'
          )}
        >
          <div className="flex items-center gap-8">
            <h3 className="text-sm font-black text-slate-800 dark:text-slate-200 uppercase tracking-[0.2em]">
              实例矩阵
            </h3>
            <Tabs
              value={viewMode}
              onValueChange={v => setViewMode(v as any)}
              className="hidden sm:block"
            >
              <TabsList
                className={cn(
                  'border border-slate-200 bg-slate-100 dark:border-white/5 dark:bg-white/5',
                  compact ? 'h-8 rounded-md p-1' : 'h-10 rounded-2xl p-1.5'
                )}
              >
                <TabsTrigger
                  value="grid"
                  className={cn(
                    'data-[state=active]:bg-white dark:data-[state=active]:bg-blue-600 data-[state=active]:text-white data-[state=active]:shadow-xl transition-all text-[10px] font-black uppercase tracking-widest',
                    compact ? 'h-6 rounded px-3' : 'h-7 rounded-xl px-4'
                  )}
                >
                  网格化
                </TabsTrigger>
                <TabsTrigger
                  value="table"
                  className={cn(
                    'data-[state=active]:bg-white dark:data-[state=active]:bg-blue-600 data-[state=active]:text-white data-[state=active]:shadow-xl transition-all text-[10px] font-black uppercase tracking-widest',
                    compact ? 'h-6 rounded px-3' : 'h-7 rounded-xl px-4'
                  )}
                >
                  列表式
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
          <div className="flex items-center gap-4">
            {runs.length === 0 && (
              <Button
                variant="ghost"
                size="sm"
                className="text-[10px] font-bold text-slate-400 hover:text-blue-500 uppercase tracking-widest"
                onClick={() => setShowDemo(!showDemo)}
              >
                {showDemo ? '卸载演示' : '辅助演示'}
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={() => reexecuteQuery()}
              className={cn(
                'border-slate-200 bg-white text-[10px] font-black uppercase tracking-widest shadow-sm transition-all hover:shadow-md active:scale-95 dark:border-white/10 dark:bg-transparent',
                compact ? 'h-8 rounded-md px-3' : 'h-10 rounded-2xl px-6'
              )}
            >
              <RefreshCw
                className={`h-4 w-4 mr-2 ${fetching ? 'animate-spin' : ''}`}
              />
              同步状态
            </Button>
          </div>
        </div>

        {viewMode === 'grid' ? (
          <div
            className={cn(
              'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3',
              compact ? 'gap-3' : 'gap-6'
            )}
          >
            {/* Premium New Deployment Card */}
            <Card
              onClick={() => setLocation('/strategies/run')}
              className={cn(
                'group relative flex cursor-pointer flex-col items-center justify-center border border-dashed border-white/5 bg-[#0B1120]/30 p-4 transition-all duration-300 hover:border-white/10 hover:bg-[#0B1120]/60',
                compact
                  ? 'min-h-[150px] rounded-lg'
                  : 'min-h-[180px] rounded-[1.25rem]'
              )}
            >
              <div className="w-10 h-10 rounded-lg bg-[#0F1729] border border-white/5 flex items-center justify-center text-slate-500 group-hover:scale-110 group-hover:text-blue-400 group-hover:border-blue-500/30 transition-all duration-300 mb-3 shadow-xl">
                <Plus size={18} strokeWidth={1.5} />
              </div>

              <div className="space-y-1.5 text-center relative z-10">
                <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wide group-hover:text-blue-400 transition-colors">
                  新建策略实例
                </h3>
                <p className="text-[9px] font-medium text-slate-600 max-w-[150px] mx-auto leading-relaxed opacity-80 group-hover:text-slate-500 transition-colors">
                  绑定单一 A 股标的并部署新的策略实例。
                </p>
              </div>
            </Card>

            {runsWithInstances.map(({ run, instance }) => (
              <StrategyInstanceCard
                key={run.id}
                run={run}
                instance={instance}
                onDelete={handleDeleteRequest}
              />
            ))}
          </div>
        ) : (
          <div
            className={cn(
              'overflow-hidden border border-slate-200 bg-white shadow-sm dark:border-white/5 dark:bg-slate-900/40',
              compact ? 'rounded-lg' : 'rounded-[2rem]'
            )}
          >
            {runsWithInstances.map(({ run, instance }, idx) => {
              const isProfit = run.profitLoss >= 0;
              const state = getStrategyRunState(run.mode, run.status);
              const isActive = state.status === 'RUNNING';
              const statusToneClass = {
                slate: 'text-slate-400',
                blue: 'text-blue-400',
                emerald: 'text-emerald-500',
                amber: 'text-amber-500',
                rose: 'text-rose-500',
                purple: 'text-purple-400',
              }[state.color];
              const modeToneClass =
                state.mode === 'LIVE'
                  ? 'bg-rose-500/10 text-rose-500 border-rose-500/20'
                  : state.mode === 'PAPER'
                    ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20'
                    : 'bg-blue-500/10 text-blue-500 border-blue-500/20';
              return (
                <div
                  key={run.id}
                  onClick={() =>
                    setLocation(
                      `/strategies/${run.strategy?.id}/runs/${encodeURIComponent(run.id)}`
                    )
                  }
                  className={`w-full flex items-center gap-4 px-6 py-4 hover:bg-slate-50 dark:hover:bg-white/5 transition-all cursor-pointer group relative ${idx !== runs.length - 1 ? 'border-b border-slate-100 dark:border-white/5' : ''}`}
                >
                  {/* Status Indicator Sidebar */}
                  <div
                    className={`w-1 h-8 rounded-full shrink-0 transition-all opacity-20 group-hover:opacity-100 ${isActive ? 'bg-emerald-500' : 'bg-slate-500'}`}
                  />

                  {/* Identity Info */}
                  <div className="flex-1 min-w-0 flex items-center gap-6">
                    <div
                      className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 shadow-sm border transition-transform duration-500 group-hover:scale-105 ${isActive ? 'bg-primary border-primary-foreground/20 text-white' : 'bg-slate-50 dark:bg-white/5 border-slate-200 dark:border-white/10 text-slate-400'}`}
                    >
                      <Activity
                        size={18}
                        className={isActive ? 'animate-pulse' : ''}
                      />
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span
                          className={cn(
                            'px-1.5 py-0.5 rounded text-[9px] font-black border uppercase tracking-tighter',
                            modeToneClass
                          )}
                        >
                          {state.modeLabel}
                        </span>
                        <h3 className="text-[13px] font-black text-slate-900 dark:text-white truncate uppercase tracking-tight">
                          {run.strategy?.name}
                        </h3>
                      </div>
                      <code className="text-[10px] font-mono text-slate-400 dark:text-slate-500 truncate block">
                        {instance.instrumentCode}
                      </code>
                    </div>
                  </div>

                  {/* Summary Stats */}
                  <div className="flex items-center gap-8 shrink-0 pr-4">
                    <div className="flex flex-col text-[10px] font-bold min-w-[80px]">
                      <span className="text-[8px] font-black text-slate-400 uppercase tracking-widest opacity-60 mb-1">
                        盈亏
                      </span>
                      <span
                        className={`flex items-center gap-1 ${isProfit ? 'text-emerald-500' : 'text-rose-500'}`}
                      >
                        {run.profitLoss.toFixed(2)}
                      </span>
                    </div>
                    <div className="flex flex-col text-[10px] font-bold text-right min-w-[70px]">
                      <span className="text-[8px] font-black text-slate-400 uppercase tracking-widest opacity-60 mb-1">
                        实例状态
                      </span>
                      <span className={statusToneClass}>
                        {state.statusLabel}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      {state.canDelete && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-rose-500 hover:bg-rose-500/10 rounded-xl text-[9px] font-black uppercase tracking-widest px-3 h-8"
                          onClick={e => {
                            e.stopPropagation();
                            handleDeleteRequest(run.id);
                          }}
                        >
                          删除
                        </Button>
                      )}
                      <div className="group-hover:translate-x-1 group-hover:-translate-y-1 transition-transform duration-300">
                        <ArrowUpRight
                          size={20}
                          className="text-slate-300 group-hover:text-primary"
                        />
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 删除确认对话框 */}
      <ConfirmDialog
        open={deleteConfirm.open}
        onOpenChange={open => {
          if (!open) setDeleteError(null);
          setDeleteConfirm({ open, runId: open ? deleteConfirm.runId : null });
        }}
        title="确认删除"
        description={
          <div className="space-y-3">
            <p>
              确定要删除这个策略实例记录吗？此操作会清理关联的回测、策略意图和状态记录。
            </p>
            {deleteError && (
              <p className="rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-xs font-bold text-rose-400">
                {deleteError}
              </p>
            )}
          </div>
        }
        confirmText="删除"
        loadingText="删除中..."
        cancelText="取消"
        variant="destructive"
        loading={deletingRun}
        onConfirm={handleDeleteConfirm}
      />
    </div>
  );
}
