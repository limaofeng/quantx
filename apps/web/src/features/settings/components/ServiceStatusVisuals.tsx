import {
  AlertTriangle,
  CheckCircle2,
  CircleOff,
  HelpCircle,
  XCircle,
  type LucideIcon,
} from 'lucide-react';
import { useMemo } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import type {
  MonitorHistory,
  MonitorHistoryPoint,
  MonitorStatus,
  MonitorTargetSummary,
} from '@/features/system/monitor-api';
import { cn } from '@/utils/cn';

import {
  formatTime,
  historyTone,
  statusLabel,
  statusPriority,
} from './service-status-presentation';

const placeholderBars = Array.from({ length: 28 }, (_, index) => index);

interface HistoryBlock {
  start: string;
  end: string;
  status: MonitorStatus;
}

function compressHistory(
  points: MonitorHistoryPoint[],
  bucketSeconds: number,
  limit = 84
) {
  const compressed: HistoryBlock[] = [];
  const chunkSize = Math.max(1, Math.ceil(points.length / limit));
  for (let index = 0; index < points.length; index += chunkSize) {
    const chunk = points.slice(index, index + chunkSize);
    const worst = chunk.reduce((current, point) =>
      statusPriority[point.status] > statusPriority[current.status]
        ? point
        : current
    );
    compressed.push({
      start: chunk[0].start,
      end: new Date(
        new Date(chunk[chunk.length - 1].start).getTime() + bucketSeconds * 1000
      ).toISOString(),
      status: worst.status,
    });
  }
  return compressed;
}

export function StatusIcon({
  status,
  className,
  decorative = false,
}: {
  status: MonitorStatus;
  className?: string;
  decorative?: boolean;
}) {
  let Icon: LucideIcon = CheckCircle2;
  let tone = 'text-emerald-300';

  if (status === 'degraded') {
    Icon = AlertTriangle;
    tone = 'text-amber-300';
  } else if (status === 'unavailable') {
    Icon = XCircle;
    tone = 'text-rose-300';
  } else if (status === 'unknown') {
    Icon = HelpCircle;
    tone = 'text-amber-300';
  } else if (status === 'disabled') {
    Icon = CircleOff;
    tone = 'text-slate-500';
  }

  return (
    <span
      aria-hidden={decorative || undefined}
      className={cn('shrink-0', tone, className)}
    >
      <Icon className="h-full w-full" aria-hidden="true" />
      <span className="sr-only">{statusLabel[status]}</span>
    </span>
  );
}

export function HistoryStrip({
  target,
  history,
  loading,
  error,
}: {
  target: MonitorTargetSummary;
  history: MonitorHistory | undefined;
  loading: boolean;
  error: boolean;
}) {
  const points = useMemo(
    () =>
      history ? compressHistory(history.points, history.bucketSeconds) : [],
    [history]
  );
  const counts = useMemo(
    () =>
      (history?.points ?? []).reduce<Record<MonitorStatus, number>>(
        (result, point) => ({
          ...result,
          [point.status]: result[point.status] + 1,
        }),
        { healthy: 0, degraded: 0, unavailable: 0, unknown: 0, disabled: 0 }
      ),
    [history]
  );
  const description =
    points.length === 0
      ? `${target.name} 当前范围没有历史样本`
      : `${target.name} 历史状态：${history?.points.length ?? 0} 个时间段，正常 ${counts.healthy}，降级 ${counts.degraded}，不可用 ${counts.unavailable}，未知 ${counts.unknown}，未启用 ${counts.disabled}`;
  const latestDescription = `${target.name} 最新采样：${statusLabel[target.status]} · ${formatTime(target.checkedAt)}`;

  return (
    <div className="flex min-w-0 flex-1 items-center gap-2">
      <div
        role="img"
        aria-label={description}
        aria-busy={loading}
        title="历史色块表示各区间内的最差状态，右侧单列最新采样"
        className={cn(
          'flex h-6 min-w-0 flex-1 items-stretch gap-px overflow-hidden rounded-sm',
          loading && points.length === 0 && 'motion-safe:animate-pulse',
          error && 'opacity-60'
        )}
      >
        {points.length > 0
          ? points.map((point, index) => (
              <span
                key={`${point.start}-${index}`}
                aria-hidden="true"
                title={`${formatTime(point.start)} – ${formatTime(point.end)} · 区间最差：${statusLabel[point.status]}`}
                className={cn(
                  'min-w-0 flex-1 rounded-sm',
                  historyTone[point.status]
                )}
              />
            ))
          : placeholderBars.map(index => (
              <span
                key={index}
                aria-hidden="true"
                className={cn(
                  'min-w-0 flex-1 rounded-sm',
                  error ? 'bg-rose-500/20' : 'bg-slate-800/80'
                )}
              />
            ))}
      </div>
      {error && (
        <span
          aria-label={`${target.name} 历史更新失败`}
          title="历史更新失败"
          className="shrink-0 text-amber-300"
        >
          <AlertTriangle className="h-4 w-4" aria-hidden="true" />
        </span>
      )}
      <div className="flex shrink-0 items-center gap-1.5 border-l border-white/10 pl-2">
        <span className="text-ui-caption text-slate-500">最新</span>
        <span
          role="img"
          aria-label={latestDescription}
          title={latestDescription}
          className={cn(
            'h-6 w-2 shrink-0 rounded-sm',
            historyTone[target.status]
          )}
        />
      </div>
    </div>
  );
}

export function LatencyChart({ history }: { history: MonitorHistory }) {
  const chartData = useMemo(
    () =>
      history.points.map(point => ({
        time: point.start,
        p50: point.latencyP50Ms,
        p95: point.latencyP95Ms,
      })),
    [history]
  );
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={chartData}>
        <CartesianGrid stroke="var(--studio-border)" vertical={false} />
        <XAxis
          dataKey="time"
          stroke="var(--studio-text-subtle)"
          tick={{ fontSize: 11 }}
          minTickGap={72}
          tickFormatter={value =>
            new Date(String(value)).toLocaleString('zh-CN', {
              month: '2-digit',
              day: '2-digit',
              hour: '2-digit',
              minute: '2-digit',
              hour12: false,
            })
          }
        />
        <YAxis
          stroke="var(--studio-text-subtle)"
          tick={{ fontSize: 11 }}
          unit=" ms"
          width={64}
        />
        <Tooltip
          labelFormatter={value => formatTime(String(value))}
          contentStyle={{
            background: 'var(--studio-panel-muted)',
            border: '1px solid var(--studio-border)',
            borderRadius: 8,
            color: 'var(--studio-text-secondary)',
            fontSize: 12,
          }}
        />
        <Line
          type="monotone"
          dataKey="p50"
          name="P50"
          stroke="var(--studio-success)"
          dot={false}
          strokeWidth={2}
          isAnimationActive={false}
        />
        <Line
          type="monotone"
          dataKey="p95"
          name="P95"
          stroke="var(--studio-warning)"
          dot={false}
          strokeWidth={1.5}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
