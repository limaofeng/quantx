import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Filter,
  HeartPulse,
} from 'lucide-react';
import { useState } from 'react';

import {
  AccountSafetyHistoryRange,
  AccountSafetyHistoryStatus,
  type TradingSafety_AccountExecutionSafetyHistoryQuery,
} from '@/generated/gql/graphql';
import { cn } from '@/utils/cn';

import { getAccountExecutionGatePresentation } from './accountExecutionGatePresentation';

type AccountSafetyHistory =
  TradingSafety_AccountExecutionSafetyHistoryQuery['accountExecutionSafetyHistory'];
type HistoryCheck = AccountSafetyHistory['checks'][number];
type HistoryIncident = AccountSafetyHistory['incidents'][number];

const historyRanges: Array<{
  value: AccountSafetyHistoryRange;
  label: string;
}> = [
  { value: AccountSafetyHistoryRange.Hours_24, label: '24 小时' },
  { value: AccountSafetyHistoryRange.Days_7, label: '7 天' },
  { value: AccountSafetyHistoryRange.Days_30, label: '30 天' },
  { value: AccountSafetyHistoryRange.Days_90, label: '90 天' },
  { value: AccountSafetyHistoryRange.Year_1, label: '1 年' },
];

const historyStatusPresentation: Record<
  AccountSafetyHistoryStatus,
  { badge: string; dot: string; label: string }
> = {
  [AccountSafetyHistoryStatus.Passed]: {
    badge: 'border-emerald-400/15 bg-emerald-400/10 text-emerald-300',
    dot: 'bg-emerald-400',
    label: '通过',
  },
  [AccountSafetyHistoryStatus.Standby]: {
    badge: 'border-primary/25 bg-primary/10 text-blue-200',
    dot: 'bg-primary',
    label: '休市待机',
  },
  [AccountSafetyHistoryStatus.Failed]: {
    badge: 'border-rose-400/25 bg-rose-400/10 text-rose-300',
    dot: 'bg-rose-400',
    label: '异常',
  },
  [AccountSafetyHistoryStatus.Unknown]: {
    badge: 'border-slate-600/40 bg-slate-700/25 text-slate-400',
    dot: 'bg-slate-600',
    label: '未观测',
  },
};

function formatHistoryTime(value: string | null | undefined) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
}

function formatRelativeTime(value: string | null | undefined, now: number) {
  if (!value) return '尚无观测';
  const ageSeconds = Math.max(
    0,
    Math.floor((now - new Date(value).getTime()) / 1000)
  );
  if (ageSeconds < 60) return `${ageSeconds} 秒前`;
  const minutes = Math.floor(ageSeconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

function formatIncidentDuration(
  openedAt: string,
  resolvedAt: string | null | undefined,
  now: number
) {
  const end = resolvedAt ? new Date(resolvedAt).getTime() : now;
  const minutes = Math.max(
    1,
    Math.round((end - new Date(openedAt).getTime()) / 60_000)
  );
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.round((minutes / 60) * 10) / 10;
  if (hours < 24) return `${hours} 小时`;
  return `${Math.round((hours / 24) * 10) / 10} 天`;
}

function getDateGroup(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'long',
    day: 'numeric',
    weekday: 'short',
  }).format(new Date(value));
}

function HistoryMetric({
  label,
  tone = 'neutral',
  value,
}: {
  label: string;
  tone?: 'success' | 'warning' | 'neutral';
  value: string;
}) {
  return (
    <div className="rounded-lg border border-slate-700/55 bg-slate-900/30 px-3 py-2.5">
      <p className="text-ui-caption text-slate-500">{label}</p>
      <p
        className={cn(
          'mt-1 font-mono text-ui-title font-semibold tabular-nums',
          tone === 'success'
            ? 'text-emerald-300'
            : tone === 'warning'
              ? 'text-amber-200'
              : 'text-slate-200'
        )}
      >
        {value}
      </p>
    </div>
  );
}

function AffectedChecksList({
  checks,
  selectedCode,
  setSelectedCode,
  unaffectedCount,
}: {
  checks: readonly HistoryCheck[];
  selectedCode: string | null;
  setSelectedCode: (value: string | null) => void;
  unaffectedCount: number;
}) {
  return (
    <div className="space-y-2">
      {checks.map(check => {
        const presentation = getAccountExecutionGatePresentation(check.code);
        const current = historyStatusPresentation[check.currentStatus];
        const selected = selectedCode === check.code;
        return (
          <button
            key={check.code}
            type="button"
            aria-pressed={selected}
            onClick={() => setSelectedCode(selected ? null : check.code)}
            className={cn(
              'flex min-h-10 w-full cursor-pointer items-center gap-2 rounded-md border px-2.5 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70',
              selected
                ? 'border-primary/40 bg-primary/10'
                : 'border-slate-700/50 bg-slate-950/20 hover:border-slate-600'
            )}
          >
            <span
              className={cn('h-2 w-2 shrink-0 rounded-sm', current.dot)}
              aria-hidden="true"
            />
            <span className="min-w-0 flex-1 truncate text-ui-label font-medium text-slate-200">
              {presentation.label}
            </span>
            <span className="shrink-0 font-mono text-ui-caption tabular-nums text-slate-400">
              {check.incidentCount} 次
            </span>
            <span
              className={cn(
                'shrink-0 rounded-md border px-1.5 py-0.5 text-ui-caption',
                current.badge
              )}
            >
              {current.label}
            </span>
          </button>
        );
      })}
      <div className="rounded-md border border-border bg-slate-950/20 px-2.5 py-2 text-ui-label text-slate-500">
        其余 {unaffectedCount} 项无异常
      </div>
    </div>
  );
}

function IncidentCard({
  incident,
  now,
}: {
  incident: HistoryIncident;
  now: number;
}) {
  const presentation = getAccountExecutionGatePresentation(incident.checkCode);
  const stateLabel = incident.active
    ? incident.observationFresh
      ? '处理中'
      : '未恢复 · 观测中断'
    : '已恢复';
  return (
    <article
      aria-label={`${presentation.label}：${stateLabel}`}
      className="relative overflow-hidden rounded-lg border border-slate-700/50 bg-slate-900/30 p-3 pl-4"
    >
      <span
        className="absolute inset-y-0 left-0 w-0.5 bg-rose-400"
        aria-hidden="true"
      />
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-rose-400/30 bg-rose-400/10 text-rose-300">
          <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 className="text-ui-body font-medium text-slate-100">
              {presentation.label}
            </h4>
            <span
              className={cn(
                'rounded-md border px-1.5 py-0.5 text-ui-caption font-medium',
                incident.active
                  ? incident.observationFresh
                    ? 'border-rose-400/25 bg-rose-400/10 text-rose-300'
                    : 'border-slate-600/50 bg-slate-700/30 text-slate-300'
                  : 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300'
              )}
            >
              {stateLabel}
            </span>
          </div>
          <p className="mt-1 text-ui-label leading-5 text-slate-400">
            {incident.lastMessage}
          </p>
          <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-ui-caption tabular-nums text-slate-500">
            <Clock3 className="h-3 w-3" aria-hidden="true" />
            <span>{formatHistoryTime(incident.openedAt)} 开始</span>
            <span aria-hidden="true">·</span>
            <span>
              {incident.resolvedAt
                ? `${formatHistoryTime(incident.resolvedAt)} 恢复`
                : '尚未确认恢复'}
            </span>
            <span aria-hidden="true">·</span>
            <span>
              持续{' '}
              {formatIncidentDuration(
                incident.openedAt,
                incident.resolvedAt,
                now
              )}
            </span>
          </p>
        </div>
      </div>
    </article>
  );
}

export function AccountExecutionSafetyHistoryView({
  fetching,
  history,
  now,
  range,
  selectedCode,
  setRange,
  setSelectedCode,
}: {
  fetching: boolean;
  history?: AccountSafetyHistory;
  now: number;
  range: AccountSafetyHistoryRange;
  selectedCode: string | null;
  setRange: (value: AccountSafetyHistoryRange) => void;
  setSelectedCode: (value: string | null) => void;
}) {
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);
  const allIncidents = history?.incidents ?? [];
  const incidents = allIncidents
    .filter(incident => !selectedCode || incident.checkCode === selectedCode)
    .sort(
      (left, right) =>
        new Date(right.openedAt).getTime() - new Date(left.openedAt).getTime()
    );
  const groupedIncidents = incidents.reduce<Record<string, HistoryIncident[]>>(
    (groups, incident) => {
      const day = getDateGroup(incident.openedAt);
      (groups[day] ??= []).push(incident);
      return groups;
    },
    {}
  );
  const affectedChecks = [...(history?.checks ?? [])]
    .filter(check => check.incidentCount > 0)
    .sort(
      (left, right) =>
        right.incidentCount - left.incidentCount ||
        getAccountExecutionGatePresentation(left.code).label.localeCompare(
          getAccountExecutionGatePresentation(right.code).label,
          'zh-CN'
        )
    );
  const unaffectedCount = Math.max(
    0,
    (history?.checks.length ?? 0) - affectedChecks.length
  );
  const activeCount = allIncidents.filter(incident => incident.active).length;
  const resolvedCount = allIncidents.length - activeCount;

  return (
    <div className="mt-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div
          className="inline-flex max-w-full overflow-x-auto rounded-lg border border-border bg-slate-950/30 p-0.5"
          aria-label="历史范围"
        >
          {historyRanges.map(option => (
            <button
              key={option.value}
              type="button"
              aria-pressed={range === option.value}
              onClick={() => {
                setRange(option.value);
                setSelectedCode(null);
              }}
              className={cn(
                'min-h-8 shrink-0 cursor-pointer rounded-md px-2.5 py-1 text-ui-label transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70',
                range === option.value
                  ? 'bg-primary/15 text-blue-100'
                  : 'text-slate-500 hover:text-slate-300'
              )}
            >
              {option.label}
            </button>
          ))}
        </div>
        {selectedCode && (
          <button
            type="button"
            onClick={() => setSelectedCode(null)}
            className="min-h-8 cursor-pointer rounded-md border border-primary/30 px-2.5 py-1 text-ui-label text-primary transition-colors hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70"
          >
            清除筛选
          </button>
        )}
      </div>

      {!history?.available ? (
        <div className="rounded-lg border border-slate-700/60 bg-slate-900/30 p-ui-section">
          <p className="text-ui-body font-medium text-slate-200">
            {fetching ? '正在读取准入历史…' : '准入历史暂不可用'}
          </p>
          {!fetching && (
            <p className="mt-1 text-ui-label leading-5 text-slate-400">
              主系统当前判定不受影响。可前往
              <a
                href="/settings/status"
                className="mx-1 text-primary underline-offset-4 hover:underline"
              >
                服务状态
              </a>
              检查独立 Monitor。
            </p>
          )}
        </div>
      ) : (
        <>
          {!history.observerFresh && (
            <div className="flex items-start gap-2 rounded-lg border border-slate-600/60 bg-slate-800/50 px-3 py-2 text-ui-label leading-5 text-slate-300">
              <Clock3
                className="mt-0.5 h-3.5 w-3.5 shrink-0"
                aria-hidden="true"
              />
              Monitor
              最近没有拿到新观测；历史事件仍保留，未观测不等于准入失败，也不能据此确认活动事件已经恢复。
            </div>
          )}

          <div className="grid grid-cols-2 gap-2 xl:grid-cols-4">
            <HistoryMetric
              label="处理中"
              tone={activeCount ? 'warning' : 'neutral'}
              value={String(activeCount)}
            />
            <HistoryMetric
              label="已恢复"
              tone="success"
              value={String(resolvedCount)}
            />
            <HistoryMetric
              label="最近观测"
              value={formatRelativeTime(history.lastObservedAt, now)}
            />
            <HistoryMetric
              label="Monitor"
              tone={history.observerFresh ? 'success' : 'neutral'}
              value={history.observerFresh ? '正常' : '观测中断'}
            />
          </div>

          <div className="lg:hidden">
            <button
              type="button"
              aria-expanded={mobileFiltersOpen}
              aria-controls="mobile-history-check-filters"
              onClick={() => setMobileFiltersOpen(current => !current)}
              className="flex min-h-12 w-full cursor-pointer items-center gap-3 rounded-lg border border-slate-700/50 bg-slate-900/30 px-3 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70"
            >
              <Filter
                className="h-4 w-4 shrink-0 text-slate-400"
                aria-hidden="true"
              />
              <span className="min-w-0 flex-1">
                <span className="block text-ui-body font-medium text-slate-100">
                  {selectedCode
                    ? getAccountExecutionGatePresentation(selectedCode).label
                    : '全部检查项'}
                </span>
                <span className="block text-ui-label text-slate-500">
                  {affectedChecks.length} 项曾异常 · 其余 {unaffectedCount}{' '}
                  项无异常
                </span>
              </span>
              <ChevronDown
                className={cn(
                  'h-4 w-4 shrink-0 text-slate-500 transition-transform duration-150 motion-reduce:transition-none',
                  mobileFiltersOpen && 'rotate-180'
                )}
                aria-hidden="true"
              />
            </button>
            {mobileFiltersOpen && (
              <div
                id="mobile-history-check-filters"
                className="mt-2 rounded-lg border border-slate-700/50 bg-slate-900/30 p-2"
              >
                <AffectedChecksList
                  checks={affectedChecks}
                  selectedCode={selectedCode}
                  setSelectedCode={setSelectedCode}
                  unaffectedCount={unaffectedCount}
                />
              </div>
            )}
          </div>

          <div className="grid items-start gap-3 lg:grid-cols-3">
            <section
              aria-labelledby="safety-history-incidents-title"
              className="min-w-0 rounded-lg border border-slate-700/50 bg-slate-950/20 p-3 lg:col-span-2"
            >
              <div>
                <h3
                  id="safety-history-incidents-title"
                  className="text-ui-body font-medium text-slate-100"
                >
                  异常事件
                </h3>
                <p className="mt-1 text-ui-label leading-5 text-slate-500">
                  只有明确失败才形成事件；休市待机和未观测不计入异常。
                </p>
              </div>
              {incidents.length === 0 ? (
                <div className="mt-3 flex items-start gap-2 rounded-lg border border-emerald-400/15 bg-emerald-400/5 px-3 py-3">
                  <CheckCircle2
                    className="mt-0.5 h-4 w-4 shrink-0 text-emerald-300"
                    aria-hidden="true"
                  />
                  <div>
                    <p className="text-ui-label font-medium text-emerald-200">
                      所选范围内没有确认的准入异常。
                    </p>
                    <p className="mt-1 text-ui-caption text-slate-500">
                      休市待机属于预期状态，不会在这里形成事件。
                    </p>
                  </div>
                </div>
              ) : (
                <div className="mt-3 space-y-ui-section">
                  {Object.entries(groupedIncidents).map(
                    ([day, dayIncidents]) => (
                      <section key={day}>
                        <h4 className="text-ui-label font-medium text-slate-400">
                          {day}
                        </h4>
                        <div className="mt-2 space-y-2">
                          {dayIncidents.map(incident => (
                            <IncidentCard
                              key={incident.id}
                              incident={incident}
                              now={now}
                            />
                          ))}
                        </div>
                      </section>
                    )
                  )}
                </div>
              )}
              {history.incidentsTruncated && (
                <p className="mt-3 text-ui-caption text-slate-500">
                  当前范围事件较多，仅显示 Monitor 返回的最近记录。
                </p>
              )}
            </section>

            <aside className="hidden space-y-3 lg:block">
              <section className="rounded-lg border border-slate-700/50 bg-slate-950/20 p-3">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <h3 className="text-ui-body font-medium text-slate-100">
                      受影响检查项
                    </h3>
                    <p className="mt-1 text-ui-label text-slate-500">
                      选择一项筛选左侧事件。
                    </p>
                  </div>
                  {selectedCode && (
                    <button
                      type="button"
                      onClick={() => setSelectedCode(null)}
                      className="cursor-pointer text-ui-caption text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70"
                    >
                      清除
                    </button>
                  )}
                </div>
                <div className="mt-3">
                  <AffectedChecksList
                    checks={affectedChecks}
                    selectedCode={selectedCode}
                    setSelectedCode={setSelectedCode}
                    unaffectedCount={unaffectedCount}
                  />
                </div>
              </section>

              <section className="rounded-lg border border-slate-700/50 bg-slate-950/20 p-3">
                <div className="flex items-center gap-2">
                  <HeartPulse
                    className={cn(
                      'h-4 w-4',
                      history.observerFresh
                        ? 'text-emerald-300'
                        : 'text-slate-400'
                    )}
                    aria-hidden="true"
                  />
                  <h3 className="text-ui-body font-medium text-slate-100">
                    {history.observerFresh ? '观测连续' : '观测已中断'}
                  </h3>
                </div>
                <dl className="mt-3 space-y-2 text-ui-label">
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-slate-500">开始观测</dt>
                    <dd className="font-mono tabular-nums text-slate-300">
                      {formatHistoryTime(history.firstObservedAt)}
                    </dd>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-slate-500">最近观测</dt>
                    <dd className="font-mono tabular-nums text-slate-300">
                      {formatHistoryTime(history.lastObservedAt)}
                    </dd>
                  </div>
                </dl>
                <p className="mt-3 text-ui-caption leading-5 text-slate-500">
                  历史仅作证据留存，当前交易决策以主系统实时状态为准。
                </p>
              </section>
            </aside>
          </div>

          <div className="flex items-center gap-2 rounded-lg border border-slate-700/50 bg-slate-950/20 px-3 py-2 text-ui-caption text-slate-500 lg:hidden">
            <HeartPulse className="h-3.5 w-3.5" aria-hidden="true" />
            <span>
              {history.observerFresh ? '观测连续' : '观测中断'} · 最近观测{' '}
              {formatHistoryTime(history.lastObservedAt)}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
