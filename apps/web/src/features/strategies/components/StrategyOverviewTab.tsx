import { Activity, AlertCircle, CalendarDays, Clock } from 'lucide-react';

import { Card } from '@/components/ui/card';
import {
  StrategyRunStatus,
  type StrategyCategory,
  type RiskLevel,
} from '@/generated/gql/graphql';
import {
  getCategoryName,
  getRiskLevelName,
  getRiskLevelColor,
} from '@/shared/utils/strategyHelpers';

import type { StrategyInstance } from '../domain';

interface StrategyOverviewTabProps {
  strategy: {
    name: string;
    description: string;
    category?: StrategyCategory | null;
    riskLevel?: RiskLevel | null;
  };
  activeRun?: {
    status: StrategyRunStatus;
    startTime?: string | null;
    instruments: string[];
  } | null;
  instance?: StrategyInstance | null;
  backtestRange?: {
    startTime?: string | null;
    endTime?: string | null;
  } | null;
}

export default function StrategyOverviewTab({
  strategy,
  activeRun,
  instance,
  backtestRange,
}: StrategyOverviewTabProps) {
  const runStatusLabelMap = {
    PENDING: '待启动',
    STARTING: '启动中',
    PAUSED: '已暂停',
    STOPPED: '已停止',
    COMPLETED: '已完成',
    ERROR: '异常',
  } as const;

  // 计算下次检查时间（模拟：startTime + 4小时）
  const getNextCheckTime = () => {
    if (!activeRun?.startTime) return '--';
    const next = new Date(
      new Date(activeRun.startTime).getTime() + 4 * 60 * 60 * 1000
    );
    return next.toLocaleString('zh-CN');
  };

  const formatDateOnly = (value?: string | null) => {
    if (!value) return null;
    const match = value.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (match) return `${match[1]}/${Number(match[2])}/${Number(match[3])}`;

    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? null
      : date.toLocaleDateString('zh-CN');
  };

  const backtestRangeLabel = (() => {
    const start = formatDateOnly(backtestRange?.startTime);
    const end = formatDateOnly(backtestRange?.endTime);
    if (!start || !end) return null;
    return `${start} - ${end}`;
  })();

  const runStatusLabel =
    activeRun?.status === StrategyRunStatus.Running
      ? '运行中'
      : runStatusLabelMap[
          activeRun?.status as keyof typeof runStatusLabelMap
        ] || activeRun?.status;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-ui-panel">
      {/* 策略说明 */}
      <Card className="p-ui-section bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-white/10 rounded-panel shadow-none relative overflow-hidden">
        <div className="absolute top-0 right-0 w-32 h-32 bg-blue-500/5 rounded-full blur-3xl" />
        <h3 className="text-ui-micro font-black text-blue-500 uppercase tracking-[0.3em] mb-6 italic">
          策略定义
        </h3>
        <p className="text-slate-600 dark:text-slate-400 text-ui-label font-medium leading-relaxed mb-8">
          {strategy.description || '暂无系统说明。'}
        </p>

        <div className="space-y-ui-section">
          <div className="flex items-center justify-between py-3 border-t border-slate-100 dark:border-white/5 group">
            <span className="text-ui-micro font-black text-slate-400 uppercase tracking-[0.2em] group-hover:text-blue-500 transition-colors">
              策略分类
            </span>
            <span className="text-ui-caption font-black text-slate-900 dark:text-slate-200 uppercase tracking-widest">
              {getCategoryName(strategy.category as string)}
            </span>
          </div>
          <div className="flex items-center justify-between py-3 border-t border-slate-100 dark:border-white/5 group">
            <span className="text-ui-micro font-black text-slate-400 uppercase tracking-[0.2em] group-hover:text-amber-500 transition-colors">
              风险等级
            </span>
            <span
              className={`text-ui-caption font-black uppercase tracking-widest ${getRiskLevelColor(strategy.riskLevel as string)}`}
            >
              {getRiskLevelName(strategy.riskLevel as string)}
            </span>
          </div>
          <div className="flex items-center justify-between py-3 border-t border-slate-100 dark:border-white/5 group">
            <span className="text-ui-micro font-black text-slate-400 uppercase tracking-[0.2em] group-hover:text-blue-500 transition-colors">
              绑定标的
            </span>
            <span className="text-ui-caption font-mono font-black text-slate-900 dark:text-slate-200">
              {instance?.instrumentCode || '--'}
            </span>
          </div>
        </div>
      </Card>

      {/* 运行状态 */}
      <Card className="p-ui-section bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-white/10 rounded-panel shadow-none relative overflow-hidden">
        <div className="absolute top-0 right-0 w-32 h-32 bg-emerald-500/5 rounded-full blur-3xl" />
        <h3 className="text-ui-micro font-black text-blue-500 uppercase tracking-[0.3em] mb-6 italic">
          实例运行状态
        </h3>

        {activeRun ? (
          <div className="space-y-ui-section">
            <div className="flex items-center justify-between group">
              <span className="text-ui-micro font-black text-slate-400 uppercase tracking-[0.2em] group-hover:text-blue-500 transition-colors">
                运行状态
              </span>
              <div className="flex items-center gap-2 bg-white/5 py-1 px-3 rounded-full border border-white/5 shadow-inner">
                <div
                  className={`w-1.5 h-1.5 rounded-full ${activeRun.status === StrategyRunStatus.Running ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)] animate-pulse' : 'bg-slate-500'}`}
                />
                <span
                  className={`text-ui-micro font-black uppercase tracking-widest ${activeRun.status === StrategyRunStatus.Running ? 'text-emerald-500' : 'text-slate-500'}`}
                >
                  {runStatusLabel || activeRun.status}
                </span>
              </div>
            </div>

            <div className="flex items-center justify-between group">
              <div className="flex items-center gap-3 font-black text-slate-400 group-hover:text-blue-500 transition-all">
                <Clock size={16} />
                <span className="text-ui-micro uppercase tracking-[0.2em]">
                  最近启动时间
                </span>
              </div>
              <span className="text-ui-caption font-mono font-bold text-slate-600 dark:text-slate-300">
                {activeRun.startTime
                  ? new Date(activeRun.startTime).toLocaleString('zh-CN')
                  : '--'}
              </span>
            </div>

            {backtestRangeLabel && (
              <div className="flex items-center justify-between group">
                <div className="flex items-center gap-3 font-black text-slate-400 group-hover:text-blue-500 transition-all">
                  <CalendarDays size={16} />
                  <span className="text-ui-micro uppercase tracking-[0.2em]">
                    回测数据区间
                  </span>
                </div>
                <span className="text-ui-caption font-mono font-bold text-slate-600 dark:text-slate-300">
                  {backtestRangeLabel}
                </span>
              </div>
            )}

            <div className="flex items-center justify-between group">
              <div className="flex items-center gap-3 font-black text-slate-400 group-hover:text-blue-500 transition-all">
                <Activity size={16} />
                <span className="text-ui-micro uppercase tracking-[0.2em]">
                  下一执行序列
                </span>
              </div>
              <span className="text-ui-caption font-mono font-bold text-slate-600 dark:text-slate-300 italic">
                {getNextCheckTime()}
              </span>
            </div>

            {activeRun.instruments.length > 0 && (
              <div className="pt-6 border-t border-slate-100 dark:border-white/5">
                <span className="text-ui-micro font-black text-slate-400 uppercase tracking-[0.2em] block mb-4">
                  绑定标的
                </span>
                <div className="flex flex-wrap gap-2">
                  {(instance?.instrumentCode
                    ? [instance.instrumentCode]
                    : activeRun.instruments.slice(0, 1)
                  ).map(inst => (
                    <code
                      key={inst}
                      className="text-ui-caption font-mono font-black bg-blue-500/5 dark:bg-blue-500/10 px-2.5 py-1 rounded-panel border border-blue-500/20 text-blue-600 dark:text-blue-400 shadow-sm transition-all hover:scale-105 hover:bg-blue-500/20"
                    >
                      {inst}
                    </code>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <div className="w-16 h-16 rounded-panel bg-slate-50 dark:bg-white/5 flex items-center justify-center mb-6 text-slate-300 opacity-50">
              <AlertCircle size={32} />
            </div>
            <p className="text-ui-body font-black text-slate-400 uppercase tracking-widest mb-2">
              系统离线
            </p>
            <p className="text-ui-caption text-slate-500 italic uppercase">
              请启动策略实例以查看实时遥测数据。
            </p>
          </div>
        )}
      </Card>
    </div>
  );
}
