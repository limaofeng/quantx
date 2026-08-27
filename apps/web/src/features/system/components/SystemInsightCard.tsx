import {
  AlertTriangle,
  CheckCircle2,
  RefreshCw,
  Server,
  XCircle,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { useStudioNavigate } from '@/components/studio-workspace';
import {
  getMonitorSummary,
  type MonitorStatus,
  type MonitorSummary,
} from '@/features/system/monitor-api';
import { cn } from '@/utils/cn';

function statusLabel(status: MonitorStatus) {
  if (status === 'healthy') return '正常';
  if (status === 'degraded') return '降级';
  if (status === 'unavailable') return '不可用';
  if (status === 'disabled') return '未启用';
  return '未知';
}

export function SystemInsightCard() {
  const navigate = useStudioNavigate();
  const [summary, setSummary] = useState<MonitorSummary | null>(null);
  const [error, setError] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      setSummary(await getMonitorSummary('24h'));
      setError(false);
    } catch {
      setError(true);
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(load, 30000);
    return () => window.clearInterval(timer);
  }, [load]);

  const stale =
    !summary?.lastCycleAt ||
    Date.now() - new Date(summary.lastCycleAt).getTime() > 90000;
  const effectiveStatus: MonitorStatus = error
    ? 'unavailable'
    : stale
      ? 'unknown'
      : (summary?.overallStatus ?? 'unknown');
  const activeIncidents = useMemo(
    () => summary?.targets.filter(target => target.activeIncident).length ?? 0,
    [summary]
  );

  return (
    <section
      className={cn(
        'rounded-panel border p-ui-section',
        effectiveStatus === 'healthy' &&
          'border-emerald-500/20 bg-emerald-500/5',
        (effectiveStatus === 'degraded' || effectiveStatus === 'unknown') &&
          'border-amber-500/25 bg-amber-500/5',
        effectiveStatus === 'unavailable' && 'border-rose-500/25 bg-rose-500/5'
      )}
    >
      <div className="flex flex-col gap-ui-section lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-center gap-3">
          <span
            className={cn(
              'flex h-11 w-11 items-center justify-center rounded-panel',
              effectiveStatus === 'healthy'
                ? 'bg-emerald-500/10 text-emerald-300'
                : effectiveStatus === 'unavailable'
                  ? 'bg-rose-500/10 text-rose-300'
                  : 'bg-amber-500/10 text-amber-300'
            )}
          >
            {effectiveStatus === 'healthy' ? (
              <CheckCircle2 className="h-6 w-6" />
            ) : effectiveStatus === 'unavailable' ? (
              <XCircle className="h-6 w-6" />
            ) : (
              <AlertTriangle className="h-6 w-6" />
            )}
          </span>
          <div>
            <h2 className="text-ui-heading font-semibold text-slate-100">
              {error
                ? '独立监测服务不可访问'
                : stale
                  ? '独立监测数据已经陈旧'
                  : `系统观测状态：${statusLabel(effectiveStatus)}`}
            </h2>
            <p className="mt-1 text-ui-label text-slate-500">
              {summary?.targets.length ?? 0} 个目标 · {activeIncidents}{' '}
              个活动事故 · 原始样本保留 90 天
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {(summary?.groups ?? []).map(group => (
            <span
              key={group.id}
              className="inline-flex items-center gap-2 rounded-lg border border-white/5 bg-slate-950/30 px-3 py-2 text-ui-label text-slate-400"
            >
              <Server className="h-3.5 w-3.5" />
              {group.name} · {statusLabel(group.status)}
            </span>
          ))}
          <button
            type="button"
            aria-label="刷新服务状态"
            disabled={refreshing}
            onClick={() => void load()}
            className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-lg text-slate-400 hover:bg-white/5 hover:text-slate-100 disabled:cursor-not-allowed"
          >
            <RefreshCw
              className={cn('h-4 w-4', refreshing && 'animate-spin')}
            />
          </button>
          <button
            type="button"
            onClick={() => navigate('/settings/status')}
            className="cursor-pointer rounded-lg bg-sky-500/10 px-3 py-2 text-ui-label font-medium text-sky-200 transition-colors hover:bg-sky-500/15"
          >
            查看历史
          </button>
        </div>
      </div>
    </section>
  );
}
