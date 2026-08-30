import {
  Activity,
  ChevronDown,
  Clock3,
  Gauge,
  Maximize2,
  RefreshCw,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useSearch } from 'wouter';

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
import { monitorReasonPresentation } from '@/features/system/monitor-reason';
import { cn } from '@/utils/cn';

import {
  formatTime,
  metric,
  probeExplanation,
  ranges,
  serviceHistoryPath,
  statusLabel,
  statusPriority,
} from './service-status-presentation';
import { HistoryStrip, LatencyChart, StatusIcon } from './ServiceStatusVisuals';

function detailId(targetId: string) {
  return `service-status-${targetId.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
}

function TargetRow({
  target,
  history,
  historyLoading,
  historyError,
  selected,
  onToggle,
}: {
  target: MonitorTargetSummary;
  history: MonitorHistory | undefined;
  historyLoading: boolean;
  historyError: boolean;
  selected: boolean;
  onToggle: () => void;
}) {
  const availability = metric(target.availabilityPct, '%');

  return (
    <button
      type="button"
      aria-expanded={selected}
      aria-controls={detailId(target.id)}
      onClick={onToggle}
      className={cn(
        'flex w-full cursor-pointer flex-col gap-2 px-3 py-2.5 text-left transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-sky-400/70 lg:flex-row lg:items-center',
        selected
          ? 'bg-sky-500/10 ring-1 ring-inset ring-sky-400/60'
          : 'hover:bg-sky-500/5'
      )}
    >
      <span className="flex w-full min-w-0 items-center gap-2 lg:w-72 lg:shrink-0">
        <StatusIcon status={target.status} className="h-5 w-5" />
        <span className="min-w-0 flex-1 lg:flex lg:items-center lg:justify-between lg:gap-3">
          <span className="block truncate text-ui-body font-medium text-slate-100">
            {target.name}
          </span>
          <span className="block shrink-0 font-mono text-ui-caption text-slate-500">
            延迟 {metric(target.latencyMs, ' ms')}
          </span>
        </span>
        <span className="ml-auto shrink-0 text-right lg:hidden">
          <span className="block font-mono text-ui-label text-slate-300">
            {availability}
          </span>
          <span className="block text-ui-caption text-slate-600">可用</span>
        </span>
        <ChevronDown
          className={cn(
            'h-4 w-4 shrink-0 text-slate-500 transition-transform duration-200 motion-reduce:transition-none lg:hidden',
            selected && 'rotate-180'
          )}
          aria-hidden="true"
        />
      </span>

      <HistoryStrip
        target={target}
        history={history}
        loading={historyLoading}
        error={historyError}
      />

      <span className="hidden w-28 shrink-0 text-right lg:block">
        <span className="block font-mono text-ui-label text-slate-300">
          {availability}
        </span>
        <span className="block text-ui-caption text-slate-600">可用</span>
      </span>
      <ChevronDown
        className={cn(
          'hidden h-4 w-4 shrink-0 text-slate-500 transition-transform duration-200 motion-reduce:transition-none lg:block',
          selected && 'rotate-180'
        )}
        aria-hidden="true"
      />
    </button>
  );
}

function TargetDetails({
  target,
  marketDataHealthy,
  history,
  historyLoading,
  historyError,
  incidents,
  incidentsLoading,
  incidentsError,
  range,
  incidentTotal,
}: {
  target: MonitorTargetSummary;
  marketDataHealthy: boolean;
  history: MonitorHistory | undefined;
  historyLoading: boolean;
  historyError: boolean;
  incidents: MonitorIncident[];
  incidentsLoading: boolean;
  incidentsError: boolean;
  range: MonitorRange;
  incidentTotal: number;
}) {
  const tradingUnavailableWithMarketData =
    target.id === 'qmt-agent' &&
    target.status === 'degraded' &&
    marketDataHealthy &&
    [
      'XTTRADING_UNAVAILABLE',
      'TRADING_RECONCILING',
      'QMT_AGENT_NOT_RECONCILED',
    ].includes(target.reasonCode ?? '');
  const currentReason = target.reasonCode
    ? monitorReasonPresentation(target.reasonCode, target.name)
    : null;
  const [, navigate] = useLocation();
  const hasLatency = history?.points.some(
    point => point.latencyP50Ms !== null || point.latencyP95Ms !== null
  );
  const hasIncidentList =
    !incidentsLoading && !incidentsError && incidents.length > 0;
  const explanation = probeExplanation(target);

  return (
    <div
      id={detailId(target.id)}
      className="grid gap-3 border-t border-sky-400/20 bg-slate-950/45 p-3 xl:grid-cols-[minmax(0,1fr)_18rem]"
    >
      <section aria-labelledby={`${detailId(target.id)}-latency`}>
        <div className="flex flex-col gap-3 border-b border-white/5 pb-3 md:flex-row md:items-start md:justify-between">
          <div>
            <h3
              id={`${detailId(target.id)}-latency`}
              className="text-ui-title font-semibold text-slate-100"
            >
              延迟趋势
            </h3>
            <p className="mt-1 max-w-3xl text-ui-caption leading-5 text-slate-500">
              {explanation}
            </p>
            {target.reasonCode && currentReason && (
              <div className="mt-2 rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2">
                <p className="text-ui-label font-medium text-amber-300">
                  当前原因：
                  {tradingUnavailableWithMarketData
                    ? '交易不可用，行情服务正常'
                    : currentReason.title}
                </p>
                <p className="mt-1 text-ui-caption leading-5 text-slate-500">
                  {currentReason.description}
                  {tradingUnavailableWithMarketData &&
                    ' 当前 XTData 行情服务正常，实盘交易仍保持阻断。'}
                </p>
                <p className="mt-1 text-ui-caption text-slate-600">
                  错误码{' '}
                  <code className="break-all font-mono text-slate-500">
                    {target.reasonCode}
                  </code>
                </p>
              </div>
            )}
          </div>
          <div className="flex shrink-0 gap-ui-section font-mono text-ui-caption text-slate-500">
            <span>覆盖 {metric(target.coveragePct, '%')}</span>
            <span>健康 {metric(target.healthyPct, '%')}</span>
            <button
              type="button"
              aria-label={`查看 ${target.name} 完整历史`}
              title="查看完整历史"
              onClick={() => navigate(serviceHistoryPath(target.id, range))}
              className="flex h-control-compact w-control-compact shrink-0 items-center justify-center rounded-control text-slate-400 hover:bg-white/5 hover:text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
            >
              <Maximize2 className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>

        <div className="mt-3 grid min-h-44 gap-3 md:grid-cols-[8rem_minmax(0,1fr)]">
          <dl className="grid grid-cols-2 gap-2 rounded-lg border border-white/5 bg-white/[0.025] p-3 md:grid-cols-1 md:content-center">
            <div>
              <dt className="text-ui-caption text-slate-500">P50</dt>
              <dd className="mt-1 font-mono text-ui-heading text-emerald-300">
                {metric(target.latencyP50Ms, ' ms')}
              </dd>
            </div>
            <div>
              <dt className="text-ui-caption text-slate-500">P95</dt>
              <dd className="mt-1 font-mono text-ui-heading text-amber-300">
                {metric(target.latencyP95Ms, ' ms')}
              </dd>
            </div>
          </dl>

          <div
            className={cn('h-48 min-w-0 md:h-44', !currentReason && 'xl:h-64')}
          >
            {historyLoading && !history ? (
              <div
                role="status"
                className="flex h-full items-center justify-center rounded-lg border border-white/5 text-ui-label text-slate-500"
              >
                正在加载历史数据…
              </div>
            ) : historyError && !history ? (
              <div
                role="alert"
                className="flex h-full items-center justify-center rounded-lg border border-amber-500/20 text-ui-label text-amber-300"
              >
                历史数据暂时不可访问
              </div>
            ) : hasLatency && history ? (
              <LatencyChart history={history} />
            ) : (
              <div className="flex h-full flex-col items-center justify-center rounded-lg border border-dashed border-white/10 text-slate-600">
                <Gauge className="h-7 w-7" aria-hidden="true" />
                <p className="mt-2 text-ui-label">当前范围没有独立延迟样本</p>
              </div>
            )}
          </div>
        </div>
      </section>

      <aside
        aria-labelledby={`${detailId(target.id)}-incidents`}
        className="flex max-h-96 min-h-0 flex-col rounded-lg border border-white/5 bg-white/[0.025] p-3 xl:max-h-80"
      >
        <div className="flex shrink-0 items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Clock3 className="h-4 w-4 text-slate-500" aria-hidden="true" />
            <h3
              id={`${detailId(target.id)}-incidents`}
              className="text-ui-title font-semibold text-slate-100"
            >
              最近事故
            </h3>
          </div>
          {hasIncidentList && (
            <span className="font-mono text-ui-caption text-slate-600">
              {incidents.length} 条
              {incidentTotal > incidents.length
                ? ` / 共 ${incidentTotal} 条`
                : ''}
            </span>
          )}
        </div>
        <div
          role={hasIncidentList ? 'region' : undefined}
          aria-label={
            hasIncidentList
              ? `最近事故列表，共 ${incidents.length} 条`
              : undefined
          }
          tabIndex={hasIncidentList ? 0 : undefined}
          className={cn(
            'mt-3 min-h-0 space-y-2',
            hasIncidentList &&
              'custom-scrollbar flex-1 overflow-y-auto overscroll-contain pr-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-sky-400/70'
          )}
        >
          {incidentsLoading ? (
            <div
              role="status"
              className="py-ui-empty text-center text-ui-label text-slate-500"
            >
              正在加载事故记录…
            </div>
          ) : incidentsError ? (
            <div
              role="alert"
              className="rounded-lg border border-amber-500/20 p-3 text-center text-ui-label text-amber-300"
            >
              事故记录暂时不可访问
            </div>
          ) : incidents.length === 0 ? (
            <div className="rounded-lg border border-dashed border-white/10 px-3 py-ui-empty text-center">
              <Activity
                className="mx-auto h-6 w-6 text-slate-600"
                aria-hidden="true"
              />
              <p className="mt-2 text-ui-label text-slate-500">
                当前范围没有事故记录
              </p>
            </div>
          ) : (
            incidents.map(incident => {
              const reasonCode =
                incident.reasonCode?.trim() || 'DEPENDENCY_NOT_READY';
              const reason = monitorReasonPresentation(
                reasonCode,
                incident.targetName
              );
              return (
                <article
                  key={incident.id}
                  className="rounded-lg border border-white/5 bg-slate-950/35 p-3"
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
                  <p className="mt-2 text-ui-label font-medium text-slate-300">
                    {reason.title}
                  </p>
                  <p className="mt-1 text-ui-caption leading-5 text-slate-500">
                    {reason.description}
                  </p>
                  <p className="mt-1 text-ui-caption text-slate-600">
                    错误码{' '}
                    <code className="break-all font-mono">{reasonCode}</code>
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
              );
            })
          )}
        </div>
      </aside>
    </div>
  );
}

function overallPresentation({
  error,
  stale,
  status,
}: {
  error: boolean;
  stale: boolean;
  status: MonitorStatus | undefined;
}) {
  if (error) {
    return {
      label: 'Monitor 当前不可访问',
      tone: 'border-amber-500/30 bg-amber-500/5',
      status: 'unknown' as const,
    };
  }
  if (stale) {
    return {
      label: '监测数据已经陈旧',
      tone: 'border-amber-500/30 bg-amber-500/5',
      status: 'unknown' as const,
    };
  }
  if (status === 'unavailable') {
    return {
      label: '检测到服务不可用',
      tone: 'border-rose-500/30 bg-rose-500/5',
      status,
    };
  }
  if (status === 'degraded') {
    return {
      label: '部分服务处于降级状态',
      tone: 'border-amber-500/30 bg-amber-500/5',
      status,
    };
  }
  if (status === 'unknown') {
    return {
      label: '部分服务状态未知',
      tone: 'border-amber-500/30 bg-amber-500/5',
      status,
    };
  }
  if (status === 'disabled') {
    return {
      label: '监测服务尚未启用',
      tone: 'border-white/10 bg-white/[0.025]',
      status,
    };
  }
  return {
    label: '所有系统运行正常',
    tone: 'border-emerald-500/20 bg-emerald-500/5',
    status: 'healthy' as const,
  };
}

export function ServiceStatusPanel() {
  const query = new URLSearchParams(useSearch());
  const [range, setRange] = useState<MonitorRange>(
    () => ranges.find(item => item.value === query.get('range'))?.value ?? '24h'
  );
  const [summary, setSummary] = useState<MonitorSummary | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(() =>
    query.get('target')
  );
  const [histories, setHistories] = useState<
    Record<string, MonitorHistory | undefined>
  >({});
  const [historyLoadingIds, setHistoryLoadingIds] = useState<Set<string>>(
    new Set()
  );
  const [historyErrorIds, setHistoryErrorIds] = useState<Set<string>>(
    new Set()
  );
  const [incidents, setIncidents] = useState<MonitorIncident[]>([]);
  const [incidentTotal, setIncidentTotal] = useState(0);
  const [incidentsLoading, setIncidentsLoading] = useState(false);
  const [incidentsError, setIncidentsError] = useState(false);
  const [error, setError] = useState(false);
  const [refreshing, setRefreshing] = useState(true);
  const selectionInitialized = useRef(false);

  const loadSummary = useCallback(async () => {
    setRefreshing(true);
    try {
      const payload = await getMonitorSummary(
        range === '24h' || range === '7d' || range === '30d' ? range : '30d'
      );
      setSummary(payload);
      setSelectedId(current => {
        if (current && payload.targets.some(target => target.id === current)) {
          return current;
        }
        if (!selectionInitialized.current) {
          selectionInitialized.current = true;
          return (
            payload.targets.find(target => target.id === 'qmt-agent')?.id ??
            payload.targets[0]?.id ??
            null
          );
        }
        return null;
      });
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
    const targetIds = summary?.targets.map(target => target.id) ?? [];
    if (targetIds.length === 0) {
      setHistories({});
      setHistoryLoadingIds(new Set());
      setHistoryErrorIds(new Set());
      return;
    }

    const controller = new AbortController();
    setHistoryLoadingIds(new Set(targetIds));
    void Promise.allSettled(
      targetIds.map(targetId =>
        getMonitorHistory(targetId, range, controller.signal)
      )
    ).then(results => {
      if (controller.signal.aborted) return;

      const failed = new Set<string>();
      setHistories(current => {
        const next = { ...current };
        results.forEach((result, index) => {
          const targetId = targetIds[index];
          if (result.status === 'fulfilled') {
            next[targetId] = result.value;
          } else {
            failed.add(targetId);
          }
        });
        return next;
      });
      setHistoryErrorIds(failed);
      setHistoryLoadingIds(new Set());
    });

    return () => controller.abort();
  }, [range, summary]);

  useEffect(() => {
    if (!selectedId) {
      setIncidents([]);
      setIncidentsLoading(false);
      setIncidentsError(false);
      return;
    }

    const controller = new AbortController();
    setIncidentsLoading(true);
    setIncidentsError(false);
    void getMonitorIncidents(range, selectedId, 1, 20, controller.signal)
      .then(nextIncidents => {
        if (controller.signal.aborted) return;
        setIncidents(nextIncidents.incidents);
        setIncidentTotal(nextIncidents.total);
      })
      .catch(errorValue => {
        if (!(
          errorValue instanceof DOMException && errorValue.name === 'AbortError'
        )) {
          setIncidents([]);
          setIncidentsError(true);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setIncidentsLoading(false);
      });
    return () => controller.abort();
  }, [range, selectedId]);

  const stale = Boolean(
    summary &&
    (!summary.lastCycleAt ||
      Date.now() - new Date(summary.lastCycleAt).getTime() > 90000)
  );
  const displayedStatus =
    summary?.targets.length === 0
      ? 'disabled'
      : summary?.groups.reduce(
          (current, group) =>
            statusPriority[group.status] > statusPriority[current]
              ? group.status
              : current,
          summary.overallStatus
        );
  const overall = overallPresentation({
    error,
    stale,
    status: displayedStatus,
  });
  const activeIncidents =
    summary?.targets.filter(target => target.activeIncident).length ?? 0;
  const initialLoading = !summary && refreshing && !error;

  return (
    <div className="space-y-ui-section">
      <header className="flex flex-col gap-ui-section xl:flex-row xl:items-end xl:justify-between">
        <div>
          <h1 className="text-ui-page-title font-semibold text-slate-100">
            服务状态
          </h1>
          <p className="mt-2 max-w-3xl text-ui-body leading-6 text-slate-400">
            独立记录外部依赖和 QuantX
            运行组件的可用性、延迟与事故历史；这些观测不会参与交易门禁。
          </p>
        </div>
        <div className="flex min-w-0 items-center gap-2">
          <div
            role="group"
            aria-label="状态历史范围"
            className="no-scrollbar flex min-w-0 flex-1 items-center gap-1 overflow-x-auto rounded-lg border border-white/5 bg-slate-950/35 p-1 xl:flex-none"
          >
            {ranges.map(item => (
              <button
                key={item.value}
                type="button"
                aria-pressed={range === item.value}
                onClick={() => setRange(item.value)}
                className={cn(
                  'h-8 shrink-0 cursor-pointer rounded-md px-3 text-ui-label font-medium transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400/70',
                  range === item.value
                    ? 'bg-sky-500/15 text-sky-200'
                    : 'text-slate-500 hover:bg-white/5 hover:text-slate-200'
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            aria-label="刷新服务状态"
            onClick={() => void loadSummary()}
            disabled={refreshing}
            className="flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-md text-slate-400 transition-colors duration-200 hover:bg-white/5 hover:text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400/70 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw
              className={cn(
                'h-4 w-4 motion-reduce:animate-none',
                refreshing && 'animate-spin'
              )}
            />
          </button>
        </div>
      </header>

      {initialLoading ? (
        <section
          role="status"
          className="flex min-h-20 items-center gap-3 rounded-panel border border-white/10 bg-white/[0.025] p-ui-section text-ui-body text-slate-400"
        >
          <RefreshCw
            className="h-6 w-6 animate-spin text-sky-300 motion-reduce:animate-none"
            aria-hidden="true"
          />
          正在加载服务状态…
        </section>
      ) : (
        <section
          role={error ? 'alert' : 'status'}
          aria-live="polite"
          className={cn('rounded-panel border p-ui-section', overall.tone)}
        >
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="flex items-center gap-3">
              <StatusIcon status={overall.status} className="h-7 w-7" />
              <div>
                <h2 className="text-ui-heading font-semibold text-slate-100">
                  {overall.label}
                </h2>
                <p className="mt-1 text-ui-label text-slate-500">
                  最近采样：{formatTime(summary?.lastCycleAt ?? null)}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-ui-section">
              <div className="flex gap-ui-section font-mono text-ui-label text-slate-400">
                <span>{summary?.targets.length ?? 0} 个目标</span>
                <span>{activeIncidents} 个活动事故</span>
              </div>
              {error && (
                <button
                  type="button"
                  onClick={() => void loadSummary()}
                  className="h-8 cursor-pointer rounded-md border border-amber-500/20 px-3 text-ui-label font-medium text-amber-200 transition-colors duration-200 hover:bg-amber-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400/70"
                >
                  重试
                </button>
              )}
            </div>
          </div>
        </section>
      )}

      {summary && summary.targets.length > 0 ? (
        <section
          aria-label="服务连续状态"
          className="overflow-hidden rounded-panel border border-white/10 bg-slate-950/20"
        >
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/5 bg-white/[0.025] px-3 py-2 text-ui-caption text-slate-500">
            <span>连续状态</span>
            <span
              className="flex flex-wrap items-center gap-3"
              aria-label="状态图例"
            >
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-sm bg-emerald-400" />
                正常
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-sm bg-amber-400" />
                降级 / 未知
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-sm bg-rose-400" />
                不可用
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-sm bg-slate-600" />
                未启用
              </span>
            </span>
          </div>

          {(summary.groups ?? []).map((group, groupIndex) => {
            const targets = summary.targets.filter(
              target => target.group === group.id
            );
            return (
              <section
                key={group.id}
                aria-labelledby={`service-group-${group.id}`}
                className={cn(groupIndex > 0 && 'border-t border-white/10')}
              >
                <div className="flex items-center justify-between gap-3 border-b border-white/5 bg-slate-900/35 px-3 py-2.5">
                  <h2
                    id={`service-group-${group.id}`}
                    className="text-ui-title font-semibold text-slate-100"
                  >
                    {group.name}
                    <span className="ml-2 text-ui-label font-normal text-slate-500">
                      · {targets.length} 个组件
                    </span>
                  </h2>
                  <span className="flex items-center gap-1.5 text-ui-caption text-slate-500">
                    <StatusIcon status={group.status} className="h-4 w-4" />
                    {statusLabel[group.status]}
                  </span>
                </div>
                <div className="divide-y divide-white/5">
                  {targets.map(target => {
                    const targetSelected = selectedId === target.id;
                    return (
                      <article key={target.id}>
                        <TargetRow
                          target={target}
                          history={histories[target.id]}
                          historyLoading={historyLoadingIds.has(target.id)}
                          historyError={historyErrorIds.has(target.id)}
                          selected={targetSelected}
                          onToggle={() =>
                            setSelectedId(current =>
                              current === target.id ? null : target.id
                            )
                          }
                        />
                        {targetSelected && (
                          <TargetDetails
                            target={target}
                            marketDataHealthy={summary.targets.some(
                              item =>
                                item.id === 'market-data' &&
                                item.status === 'healthy'
                            )}
                            history={histories[target.id]}
                            historyLoading={historyLoadingIds.has(target.id)}
                            historyError={historyErrorIds.has(target.id)}
                            incidents={incidents}
                            incidentsLoading={incidentsLoading}
                            incidentsError={incidentsError}
                            incidentTotal={incidentTotal}
                            range={range}
                          />
                        )}
                      </article>
                    );
                  })}
                </div>
              </section>
            );
          })}
        </section>
      ) : summary && !error ? (
        <section className="rounded-panel border border-dashed border-white/10 px-ui-section py-ui-empty text-center">
          <Activity
            className="mx-auto h-7 w-7 text-slate-600"
            aria-hidden="true"
          />
          <h2 className="mt-3 text-ui-title font-medium text-slate-300">
            尚未配置监测目标
          </h2>
          <p className="mt-1 text-ui-label text-slate-500">
            Monitor 返回了有效摘要，但当前没有可展示的服务。
          </p>
        </section>
      ) : null}
    </div>
  );
}
