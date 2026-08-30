import {
  Activity,
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Clock3,
  RefreshCw,
} from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useLocation, useSearch } from 'wouter';

import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type {
  MonitorIncident,
  MonitorRange,
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
} from './service-status-presentation';
import { HistoryStrip, LatencyChart, StatusIcon } from './ServiceStatusVisuals';
import { useServiceStatusHistory } from './useServiceStatusHistory';

function duration(incident: MonitorIncident, now: number) {
  const end = incident.resolvedAt
    ? new Date(incident.resolvedAt).getTime()
    : now;
  const seconds = Math.max(
    0,
    Math.floor((end - new Date(incident.openedAt).getTime()) / 1000)
  );
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分 ${seconds % 60} 秒`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时 ${minutes % 60} 分`;
  return `${Math.floor(hours / 24)} 天 ${hours % 24} 小时`;
}

function IncidentItem({
  incident,
  now,
}: {
  incident: MonitorIncident;
  now: number;
}) {
  const reasonCode = incident.reasonCode?.trim() || 'DEPENDENCY_NOT_READY';
  const reason = monitorReasonPresentation(reasonCode, incident.targetName);
  return (
    <li
      className={cn(
        'flex flex-col gap-3 rounded-panel border border-l-4 bg-slate-950/30 p-3 xl:flex-row xl:items-center',
        incident.active
          ? 'border-rose-400/25 border-l-rose-400'
          : 'border-white/5 border-l-emerald-400'
      )}
    >
      <div className="flex shrink-0 items-center gap-2 xl:w-16 xl:flex-col">
        <StatusIcon
          status={incident.active ? 'unavailable' : 'healthy'}
          className="h-5 w-5"
          decorative
        />
        <span
          className={cn(
            'text-ui-caption font-medium',
            incident.active ? 'text-rose-300' : 'text-emerald-300'
          )}
        >
          {incident.active ? '进行中' : '已恢复'}
        </span>
        <span className="font-mono text-ui-caption text-slate-400 xl:hidden">
          #{incident.id}
        </span>
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-ui-body font-medium text-slate-200">
          {reason.title}
        </p>
        <p className="mt-1 text-ui-caption leading-5 text-slate-400">
          {reason.description}
        </p>
        <p className="mt-1 text-ui-caption text-slate-500">
          错误码{' '}
          <code className="break-all font-mono text-slate-400">
            {reasonCode}
          </code>
          <span className="ml-3 hidden font-mono xl:inline">
            #{incident.id}
          </span>
        </p>
      </div>
      <dl className="grid shrink-0 grid-cols-2 gap-3 border-t border-white/5 pt-3 font-mono text-ui-caption sm:grid-cols-3 xl:w-96 xl:border-l xl:border-t-0 xl:pl-3 xl:pt-0">
        <div>
          <dt className="text-slate-500">开始时间</dt>
          <dd className="mt-1 text-slate-300">
            <time dateTime={incident.openedAt}>
              {formatTime(incident.openedAt)}
            </time>
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">恢复时间</dt>
          <dd className="mt-1 text-slate-300">
            {incident.resolvedAt ? (
              <time dateTime={incident.resolvedAt}>
                {formatTime(incident.resolvedAt)}
              </time>
            ) : (
              '尚未恢复'
            )}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">
            {incident.active ? '持续至本次刷新' : '持续时长'}
          </dt>
          <dd className="mt-1 text-slate-300">{duration(incident, now)}</dd>
        </div>
      </dl>
    </li>
  );
}

function Message({
  error,
  children,
  onRetry,
}: {
  error?: boolean;
  children: ReactNode;
  onRetry?: () => void;
}) {
  return (
    <div
      role={error ? 'alert' : 'status'}
      className={cn(
        'flex min-h-24 flex-wrap items-center justify-center gap-3 rounded-panel border border-dashed p-ui-empty text-ui-label',
        error
          ? 'border-amber-400/25 text-amber-200'
          : 'border-white/10 text-slate-400'
      )}
    >
      <span>{children}</span>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          重试
        </Button>
      )}
    </div>
  );
}

export function ServiceStatusHistoryPanel({ targetId }: { targetId: string }) {
  const search = useSearch();
  const [, navigate] = useLocation();
  const query = new URLSearchParams(search);
  const range: MonitorRange =
    ranges.find(item => item.value === query.get('range'))?.value ?? '24h';
  const rawPage = Number(query.get('page') ?? 1);
  const page =
    Number.isSafeInteger(rawPage) && rawPage >= 1 && rawPage <= 1000000
      ? rawPage
      : 1;
  const rawSize = Number(query.get('pageSize') ?? 20);
  const pageSize = [10, 20, 50].includes(rawSize) ? rawSize : 20;
  const [revision, setRevision] = useState(0);
  const listHeading = useRef<HTMLHeadingElement>(null);
  const focusAfterPage = useRef(false);
  const resources = useServiceStatusHistory(
    targetId,
    range,
    page,
    pageSize,
    revision
  );
  const summary = resources.summary.data;
  const history = resources.history.data;
  const incidentPage = resources.incidents.data;
  const target = summary?.targets.find(item => item.id === targetId);
  const reason = target?.reasonCode
    ? monitorReasonPresentation(target.reasonCode, target.name)
    : null;
  const totalPages = Math.max(
    1,
    Math.ceil((incidentPage?.total ?? 0) / pageSize)
  );
  const loadingIncidents = !incidentPage && !resources.incidents.error;
  const refreshing =
    (!summary && !resources.summary.error) ||
    (!history && !resources.history.error) ||
    loadingIncidents;
  const now = summary ? new Date(summary.generatedAt).getTime() : Date.now();
  const stale =
    summary &&
    (!summary.lastCycleAt ||
      now - new Date(summary.lastCycleAt).getTime() > 90000);

  useEffect(() => {
    if (incidentPage && page > totalPages) {
      navigate(serviceHistoryPath(targetId, range, totalPages, pageSize), {
        replace: true,
      });
    }
  }, [incidentPage, page, totalPages, targetId, range, pageSize, navigate]);

  useEffect(() => {
    if (incidentPage && focusAfterPage.current) {
      listHeading.current?.focus({ preventScroll: true });
      listHeading.current?.scrollIntoView?.({ block: 'start' });
      focusAfterPage.current = false;
    }
  }, [incidentPage]);

  function refresh() {
    navigate(serviceHistoryPath(targetId, range, 1, pageSize), {
      replace: true,
    });
    setRevision(value => value + 1);
  }

  function changePage(nextPage: number) {
    focusAfterPage.current = true;
    navigate(serviceHistoryPath(targetId, range, nextPage, pageSize));
  }

  const pageNumbers = Array.from(
    new Set([1, page - 1, page, page + 1, totalPages])
  )
    .filter(value => value >= 1 && value <= totalPages)
    .sort((a, b) => a - b);

  return (
    <div className="space-y-ui-section">
      <header className="space-y-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={() =>
            navigate(
              `/settings/status?target=${encodeURIComponent(targetId)}&range=${range}`
            )
          }
          className="text-slate-400"
        >
          <ArrowLeft aria-hidden="true" />
          服务状态
        </Button>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-ui-page-title font-semibold text-slate-100">
            {target?.name ?? '服务'} 历史
          </h1>
          {target && (
            <span className="flex items-center gap-1.5 text-ui-label text-slate-300">
              <StatusIcon
                status={target.status}
                className="h-4 w-4"
                decorative
              />
              {statusLabel[target.status]}
            </span>
          )}
          <span className="text-ui-caption text-slate-400">
            查询时间{' '}
            <span className="font-mono">
              {formatTime(summary?.generatedAt ?? null)}
            </span>
          </span>
          <Button
            variant="ghost"
            size="sm"
            aria-label="刷新历史"
            disabled={refreshing}
            onClick={refresh}
          >
            <RefreshCw
              className={cn(
                refreshing && 'animate-spin motion-reduce:animate-none'
              )}
              aria-hidden="true"
            />
            刷新
          </Button>
        </div>
      </header>

      {resources.summary.error ? (
        <Message error onRetry={resources.summary.retry}>
          Monitor 当前不可访问，无法确认当前服务状态。
        </Message>
      ) : !summary ? (
        <Message>正在加载服务状态…</Message>
      ) : (
        <section
          aria-label="快速切换指标"
          className="rounded-panel border border-white/10 bg-white/[0.025] p-ui-section"
        >
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-ui-title font-semibold">快速切换指标</h2>
            <span className="text-ui-caption text-slate-500">
              可用率为近 24 小时统计
            </span>
          </div>
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-4 2xl:grid-cols-5">
            {summary.targets.map(item => (
              <button
                key={item.id}
                type="button"
                aria-label={`切换到 ${item.name}`}
                aria-current={item.id === targetId ? 'page' : undefined}
                onClick={() =>
                  navigate(serviceHistoryPath(item.id, range, 1, pageSize))
                }
                className={cn(
                  'flex min-w-0 cursor-pointer items-center gap-2 rounded-control border p-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70',
                  item.id === targetId
                    ? 'border-blue-400/60 bg-blue-500/10'
                    : 'border-white/5 bg-slate-950/30 hover:border-blue-400/30 hover:bg-white/5'
                )}
              >
                <StatusIcon status={item.status} className="h-5 w-5" />
                <span className="min-w-0 flex-1 2xl:flex 2xl:items-center 2xl:justify-between 2xl:gap-2">
                  <span
                    className="block truncate text-ui-label font-medium text-slate-200"
                    title={item.name}
                  >
                    {item.name}
                  </span>
                  <span className="block shrink-0 font-mono text-ui-caption text-slate-400">
                    {metric(item.availabilityPct, '%')} 可用
                  </span>
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      {summary && !target ? (
        <Message error>
          未找到此监测指标，请从上方选择其他指标或返回服务状态。
        </Message>
      ) : (
        target && (
          <>
            {stale && (
              <Message error>
                监测数据已陈旧，当前状态以最后一次采样为准；请刷新后重试。
              </Message>
            )}
            <section
              aria-labelledby="service-history-overview"
              className="space-y-3 rounded-panel border border-white/10 bg-slate-950/20 p-ui-section"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2
                  id="service-history-overview"
                  className="text-ui-title font-semibold"
                >
                  概览 · {target.name}
                </h2>
                <div
                  role="group"
                  aria-label="历史时间范围"
                  className="flex flex-wrap gap-1"
                >
                  {ranges.map(item => (
                    <Button
                      key={item.value}
                      variant="ghost"
                      size="sm"
                      aria-pressed={range === item.value}
                      onClick={() =>
                        navigate(
                          serviceHistoryPath(targetId, item.value, 1, pageSize)
                        )
                      }
                      className={cn(
                        range === item.value
                          ? 'bg-blue-500/15 text-blue-200'
                          : 'text-slate-400'
                      )}
                    >
                      {item.label}
                    </Button>
                  ))}
                </div>
                <div
                  aria-label="近24小时统计"
                  className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-ui-caption text-slate-300"
                >
                  <span className="text-slate-500">近 24 小时</span>
                  <span>覆盖 {metric(target.coveragePct, '%')}</span>
                  <span>健康 {metric(target.healthyPct, '%')}</span>
                  <span>P50 {metric(target.latencyP50Ms, ' ms')}</span>
                  <span>P95 {metric(target.latencyP95Ms, ' ms')}</span>
                </div>
              </div>
              {reason && (
                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 rounded-control border border-amber-400/20 bg-amber-400/5 px-3 py-2">
                  <div>
                    <p className="text-ui-label font-medium text-amber-200">
                      当前原因：{reason.title}
                    </p>
                    <p className="mt-1 text-ui-caption leading-5 text-slate-400">
                      {reason.description}
                    </p>
                  </div>
                  <p className="text-ui-caption text-slate-500">
                    错误码{' '}
                    <code className="break-all font-mono">
                      {target.reasonCode}
                    </code>
                  </p>
                </div>
              )}
              <HistoryStrip
                target={target}
                history={history}
                loading={!history && !resources.history.error}
                error={Boolean(resources.history.error)}
              />
              <div className="flex flex-wrap items-center justify-between gap-2 text-ui-caption text-slate-400">
                <span>{probeExplanation(target)}</span>
                <span className="flex gap-3">
                  <span className="text-emerald-300">正常 / P50</span>
                  <span className="text-amber-300">降级·未知 / P95</span>
                  <span className="text-rose-300">不可用</span>
                  <span>灰色：未启用</span>
                </span>
              </div>
              {resources.history.error ? (
                <Message error onRetry={resources.history.retry}>
                  历史曲线暂时不可访问
                </Message>
              ) : !history ? (
                <Message>正在加载历史曲线…</Message>
              ) : history.points.length === 0 ? (
                <Message>
                  当前范围没有状态样本，不能推断服务正常或故障。
                </Message>
              ) : history.points.some(point => point.latencyCount > 0) ? (
                <div className="h-40 min-w-0">
                  <LatencyChart history={history} />
                </div>
              ) : (
                <Message>
                  当前范围没有独立延迟样本；状态历史仍显示在上方。
                </Message>
              )}
            </section>

            <section
              aria-labelledby="service-incident-history"
              className="space-y-3 rounded-panel border border-white/10 bg-white/[0.015] p-ui-section"
            >
              <div className="flex flex-wrap items-center gap-3">
                <Clock3 className="h-4 w-4 text-slate-400" aria-hidden="true" />
                <h2
                  id="service-incident-history"
                  ref={listHeading}
                  tabIndex={-1}
                  className="scroll-mt-3 rounded-control text-ui-title font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
                >
                  事故历史
                </h2>
                <span
                  role="status"
                  className="font-mono text-ui-caption text-slate-400"
                >
                  {incidentPage
                    ? `共 ${incidentPage.total} 条 · 第 ${Math.min(page, totalPages)} / ${totalPages} 页`
                    : resources.incidents.error
                      ? '读取失败'
                      : '正在读取记录'}
                </span>
              </div>
              <p className="text-ui-caption text-slate-500">
                按开始时间倒序，包含跨越所选范围的事故。记录受 Monitor
                保留期限制，默认保留一年。
                手动刷新获取最新记录，翻页时不自动插入新事故。只读观测，不参与交易门禁。
              </p>
              {resources.incidents.error ? (
                <Message error onRetry={resources.incidents.retry}>
                  事故记录暂时不可访问
                </Message>
              ) : loadingIncidents ? (
                <Message>正在加载事故记录…</Message>
              ) : incidentPage?.total === 0 ? (
                <div className="py-ui-empty text-center text-slate-400">
                  <Activity
                    className="mx-auto mb-2 h-6 w-6"
                    aria-hidden="true"
                  />
                  <p>当前范围没有事故记录</p>
                  <p className="mt-1 text-ui-caption">
                    没有事故记录不代表观测完整，请结合上方状态历史查看。
                  </p>
                </div>
              ) : (
                <ul aria-label="事故历史列表" className="space-y-2">
                  {incidentPage?.incidents.map(incident => (
                    <IncidentItem
                      key={incident.id}
                      incident={incident}
                      now={new Date(incidentPage.asOf).getTime()}
                    />
                  ))}
                </ul>
              )}
              <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-white/5 pt-3">
                <nav
                  aria-label="事故分页"
                  className="flex flex-wrap items-center gap-1"
                >
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={loadingIncidents || !incidentPage || page <= 1}
                    onClick={() => changePage(page - 1)}
                  >
                    <ChevronLeft aria-hidden="true" />
                    上一页
                  </Button>
                  {pageNumbers.map((value, index) => (
                    <span key={value} className="flex items-center gap-1">
                      {index > 0 && value - pageNumbers[index - 1] > 1 && (
                        <span className="px-1 text-slate-500">…</span>
                      )}
                      <Button
                        variant={value === page ? 'default' : 'ghost'}
                        size="sm"
                        aria-label={`第 ${value} 页`}
                        aria-current={value === page ? 'page' : undefined}
                        disabled={loadingIncidents || !incidentPage}
                        onClick={() => changePage(value)}
                      >
                        {value}
                      </Button>
                    </span>
                  ))}
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={
                      loadingIncidents || !incidentPage || page >= totalPages
                    }
                    onClick={() => changePage(page + 1)}
                  >
                    下一页
                    <ChevronRight aria-hidden="true" />
                  </Button>
                </nav>
                <div className="flex items-center gap-2">
                  <span className="text-ui-caption text-slate-400">每页</span>
                  <Select
                    value={String(pageSize)}
                    onValueChange={value => {
                      focusAfterPage.current = true;
                      navigate(
                        serviceHistoryPath(targetId, range, 1, Number(value))
                      );
                    }}
                  >
                    <SelectTrigger aria-label="每页事故数量" className="w-28">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {[10, 20, 50].map(value => (
                        <SelectItem key={value} value={String(value)}>
                          {value} 条
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </footer>
            </section>
          </>
        )
      )}
    </div>
  );
}
