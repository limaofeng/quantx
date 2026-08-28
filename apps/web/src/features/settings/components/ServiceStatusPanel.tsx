import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Database,
  Gauge,
  RefreshCw,
  Server,
  XCircle,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import {
  getMonitorHistory,
  getMonitorIncidents,
  getMonitorSummary,
  type MonitorHistory,
  type MonitorIncident,
  type MonitorRange,
  type MonitorStatus,
  type MonitorSummary,
  type MonitorTargetSummary,
} from '@/features/system/monitor-api';
import { cn } from '@/utils/cn';

const ranges: Array<{ value: MonitorRange; label: string }> = [
  { value: '24h', label: '24 小时' },
  { value: '7d', label: '7 天' },
  { value: '30d', label: '30 天' },
  { value: '90d', label: '90 天' },
  { value: '1y', label: '1 年' },
];

const statusLabel: Record<MonitorStatus, string> = {
  healthy: '正常',
  degraded: '降级',
  unavailable: '不可用',
  unknown: '未知',
  disabled: '未启用',
};

function statusTone(status: MonitorStatus) {
  if (status === 'healthy') return 'text-emerald-300 bg-emerald-500/10';
  if (status === 'degraded' || status === 'unknown') {
    return 'text-amber-300 bg-amber-500/10';
  }
  if (status === 'disabled') return 'text-slate-400 bg-white/5';
  return 'text-rose-300 bg-rose-500/10';
}

function metric(value: number | null, suffix = '') {
  return value === null ? 'N/A' : `${value.toFixed(2)}${suffix}`;
}

function formatTime(value: string | null) {
  if (!value) return '尚无记录';
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}

function TargetCard({
  target,
  selected,
  onSelect,
}: {
  target: MonitorTargetSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const Icon = target.group === 'external_dependency' ? Database : Server;
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={cn(
        'cursor-pointer rounded-panel border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/70',
        selected
          ? 'border-sky-400/40 bg-sky-500/10'
          : 'border-white/5 bg-slate-950/35 hover:border-white/15 hover:bg-slate-900/60'
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="flex min-w-0 items-center gap-2">
          <Icon className="h-4 w-4 shrink-0 text-slate-500" />
          <span className="truncate text-ui-label font-medium text-slate-200">
            {target.name}
          </span>
        </span>
        <span
          className={cn(
            'shrink-0 rounded-full px-2 py-0.5 text-ui-caption font-medium',
            statusTone(target.status)
          )}
        >
          {statusLabel[target.status]}
        </span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 font-mono text-ui-caption text-slate-500">
        <span>延迟 {metric(target.latencyMs, ' ms')}</span>
        <span>可用 {metric(target.availabilityPct, '%')}</span>
      </div>
    </button>
  );
}

export function ServiceStatusPanel() {
  const [range, setRange] = useState<MonitorRange>('24h');
  const [summary, setSummary] = useState<MonitorSummary | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [history, setHistory] = useState<MonitorHistory | null>(null);
  const [incidents, setIncidents] = useState<MonitorIncident[]>([]);
  const [error, setError] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const loadSummary = useCallback(async () => {
    setRefreshing(true);
    try {
      const payload = await getMonitorSummary(
        range === '24h' || range === '7d' || range === '30d' ? range : '30d'
      );
      setSummary(payload);
      setSelectedId(current => current ?? payload.targets[0]?.id ?? null);
      setError(false);
    } catch {
      setError(true);
    } finally {
      setRefreshing(false);
    }
  }, [range]);

  useEffect(() => {
    void loadSummary();
    const timer = window.setInterval(loadSummary, 30000);
    return () => window.clearInterval(timer);
  }, [loadSummary]);

  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    void Promise.all([
      getMonitorHistory(selectedId, range, controller.signal),
      getMonitorIncidents(range, selectedId, controller.signal),
    ])
      .then(([nextHistory, nextIncidents]) => {
        setHistory(nextHistory);
        setIncidents(nextIncidents);
      })
      .catch(errorValue => {
        if (!(
          errorValue instanceof DOMException && errorValue.name === 'AbortError'
        )) {
          setHistory(null);
          setIncidents([]);
        }
      });
    return () => controller.abort();
  }, [range, selectedId]);

  const selected = summary?.targets.find(target => target.id === selectedId);
  const stale =
    !summary?.lastCycleAt ||
    Date.now() - new Date(summary.lastCycleAt).getTime() > 90000;
  const chartData = useMemo(
    () =>
      (history?.points ?? []).map(point => ({
        time: new Date(point.start).toLocaleString('zh-CN', {
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
          hour12: false,
        }),
        p50: point.latencyP50Ms,
        p95: point.latencyP95Ms,
        status: point.status,
      })),
    [history]
  );

  return (
    <div className="space-y-ui-section">
      <header className="flex flex-col gap-ui-section lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-ui-label font-medium uppercase tracking-[0.22em] text-sky-400">
            Independent observability
          </p>
          <h1 className="mt-2 text-ui-page-title font-semibold text-slate-100">
            服务状态
          </h1>
          <p className="mt-2 max-w-3xl text-ui-body leading-6 text-slate-400">
            独立记录外部依赖和 QuantX
            运行组件的可用性、延迟与事故历史；这些观测不会参与交易门禁。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {ranges.map(item => (
            <button
              key={item.value}
              type="button"
              aria-pressed={range === item.value}
              onClick={() => setRange(item.value)}
              className={cn(
                'cursor-pointer rounded-lg px-3 py-1.5 text-ui-label font-medium transition-colors',
                range === item.value
                  ? 'bg-sky-500/15 text-sky-200'
                  : 'text-slate-500 hover:bg-white/5 hover:text-slate-200'
              )}
            >
              {item.label}
            </button>
          ))}
          <button
            type="button"
            aria-label="刷新服务状态"
            onClick={() => void loadSummary()}
            disabled={refreshing}
            className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-lg text-slate-400 hover:bg-white/5 hover:text-slate-100 disabled:cursor-not-allowed"
          >
            <RefreshCw
              className={cn('h-4 w-4', refreshing && 'animate-spin')}
            />
          </button>
        </div>
      </header>

      <section
        className={cn(
          'rounded-panel border p-ui-section',
          error || stale
            ? 'border-amber-500/30 bg-amber-500/5'
            : summary?.overallStatus === 'unavailable'
              ? 'border-rose-500/30 bg-rose-500/5'
              : 'border-emerald-500/20 bg-emerald-500/5'
        )}
      >
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-3">
            {error || stale ? (
              <AlertTriangle className="h-7 w-7 text-amber-300" />
            ) : summary?.overallStatus === 'unavailable' ? (
              <XCircle className="h-7 w-7 text-rose-300" />
            ) : (
              <CheckCircle2 className="h-7 w-7 text-emerald-300" />
            )}
            <div>
              <h2 className="text-ui-heading font-semibold text-slate-100">
                {error
                  ? 'Monitor 当前不可访问'
                  : stale
                    ? '监测数据已经陈旧'
                    : summary?.overallStatus === 'unavailable'
                      ? '检测到服务不可用'
                      : '监测链路正常'}
              </h2>
              <p className="mt-1 text-ui-label text-slate-500">
                最近采样：{formatTime(summary?.lastCycleAt ?? null)}
              </p>
            </div>
          </div>
          <div className="flex gap-ui-section font-mono text-ui-label text-slate-400">
            <span>{summary?.targets.length ?? 0} 个目标</span>
            <span>
              {summary?.targets.filter(target => target.activeIncident)
                .length ?? 0}{' '}
              个活动事故
            </span>
          </div>
        </div>
      </section>

      {(summary?.groups ?? []).map(group => (
        <section key={group.id}>
          <div className="mb-3 flex items-end justify-between gap-3">
            <div>
              <h2 className="text-ui-heading font-semibold text-slate-100">
                {group.name}
              </h2>
              <p className="mt-1 text-ui-label text-slate-500">
                {group.id === 'external_dependency'
                  ? '主动探测连接、协议响应和端到端延迟'
                  : 'HTTP 链路与服务端语义状态快照'}
              </p>
            </div>
            <span
              className={cn(
                'rounded-full px-2 py-1 text-ui-caption',
                statusTone(group.status)
              )}
            >
              {statusLabel[group.status]}
            </span>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {summary?.targets
              .filter(target => target.group === group.id)
              .map(target => (
                <TargetCard
                  key={target.id}
                  target={target}
                  selected={target.id === selectedId}
                  onSelect={() => setSelectedId(target.id)}
                />
              ))}
          </div>
        </section>
      ))}

      {selected && (
        <section className="grid gap-ui-section xl:grid-cols-[minmax(0,1fr)_320px]">
          <div className="rounded-panel border border-white/5 bg-slate-950/35 p-ui-section">
            <div className="flex flex-col gap-3 border-b border-white/5 pb-4 md:flex-row md:items-end md:justify-between">
              <div>
                <h2 className="text-ui-heading font-semibold text-slate-100">
                  {selected.name} 延迟趋势
                </h2>
                <p className="mt-1 text-ui-label text-slate-500">
                  {selected.probeKind === 'derived'
                    ? '该组件来自语义快照，不生成虚假的独立延迟。'
                    : selected.probeKind === 'composite'
                      ? `状态综合 Windows 健康端点与服务端会话/对账语义；延迟为 Monitor 到 Windows Agent 的健康探测 RTT。P50 ${metric(selected.latencyP50Ms, ' ms')} · P95 ${metric(selected.latencyP95Ms, ' ms')}`
                      : `P50 ${metric(selected.latencyP50Ms, ' ms')} · P95 ${metric(selected.latencyP95Ms, ' ms')}`}
                </p>
              </div>
              <div className="flex gap-ui-section font-mono text-ui-caption text-slate-500">
                <span>覆盖 {metric(selected.coveragePct, '%')}</span>
                <span>健康 {metric(selected.healthyPct, '%')}</span>
              </div>
            </div>
            <div className="mt-4 h-72">
              {chartData.some(
                point => point.p50 !== null || point.p95 !== null
              ) ? (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData}>
                    <CartesianGrid
                      stroke="rgba(148,163,184,0.08)"
                      vertical={false}
                    />
                    <XAxis
                      dataKey="time"
                      stroke="#64748b"
                      tick={{ fontSize: 10 }}
                      minTickGap={36}
                    />
                    <YAxis
                      stroke="#64748b"
                      tick={{ fontSize: 10 }}
                      unit=" ms"
                      width={64}
                    />
                    <Tooltip
                      contentStyle={{
                        background: '#0f172a',
                        border: '1px solid rgba(148,163,184,0.18)',
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                    />
                    <Line
                      type="monotone"
                      dataKey="p50"
                      name="P50"
                      stroke="#38bdf8"
                      dot={false}
                      strokeWidth={2}
                    />
                    <Line
                      type="monotone"
                      dataKey="p95"
                      name="P95"
                      stroke="#f59e0b"
                      dot={false}
                      strokeWidth={1.5}
                    />
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex h-full flex-col items-center justify-center text-slate-600">
                  <Gauge className="h-8 w-8" />
                  <p className="mt-3 text-ui-label">当前范围没有独立延迟样本</p>
                </div>
              )}
            </div>
          </div>

          <aside className="rounded-panel border border-white/5 bg-slate-950/35 p-ui-section">
            <div className="flex items-center gap-2">
              <Clock3 className="h-4 w-4 text-slate-500" />
              <h2 className="text-ui-heading font-semibold text-slate-100">
                最近事故
              </h2>
            </div>
            <div className="mt-4 space-y-3">
              {incidents.length === 0 ? (
                <div className="rounded-lg border border-dashed border-white/10 p-ui-section text-center">
                  <Activity className="mx-auto h-6 w-6 text-slate-600" />
                  <p className="mt-2 text-ui-label text-slate-500">
                    当前范围没有事故记录
                  </p>
                </div>
              ) : (
                incidents.map(incident => (
                  <article
                    key={incident.id}
                    className="rounded-lg border border-white/5 bg-white/[0.025] p-3"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span
                        className={cn(
                          'rounded px-1.5 py-0.5 text-ui-caption',
                          incident.active
                            ? 'bg-rose-500/10 text-rose-300'
                            : 'bg-emerald-500/10 text-emerald-300'
                        )}
                      >
                        {incident.active ? '进行中' : '已恢复'}
                      </span>
                      <span className="font-mono text-ui-caption text-slate-600">
                        #{incident.id}
                      </span>
                    </div>
                    <p className="mt-2 font-mono text-ui-label text-slate-300">
                      {incident.reasonCode ?? 'DEPENDENCY_NOT_READY'}
                    </p>
                    <p className="mt-2 text-ui-caption text-slate-600">
                      开始 {formatTime(incident.openedAt)}
                    </p>
                    {incident.resolvedAt && (
                      <p className="mt-1 text-ui-caption text-slate-600">
                        恢复 {formatTime(incident.resolvedAt)}
                      </p>
                    )}
                  </article>
                ))
              )}
            </div>
          </aside>
        </section>
      )}
    </div>
  );
}
