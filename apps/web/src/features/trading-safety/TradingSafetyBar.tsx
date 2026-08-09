import {
  AlertTriangle,
  CheckCircle2,
  DatabaseBackup,
  RadioTower,
  ShieldAlert,
  TimerReset,
  UserRound,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { useQuery } from 'urql';

import { StatusBar } from '@/components/studio-workbench';
import { cn } from '@/utils/cn';

import { LiveSafetyStatusQuery } from './operations';
import { useTradingSafety } from './trading-safety-context';

function ageLabel(value?: string | null) {
  if (!value) return '无记录';
  const milliseconds = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return '时间异常';
  const seconds = Math.floor(milliseconds / 1000);
  if (seconds < 60) return `${seconds} 秒前`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  return `${Math.floor(minutes / 60)} 小时前`;
}

function SafetyMetric({
  icon,
  label,
  value,
  className,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 whitespace-nowrap text-[11px] normal-case tracking-normal text-slate-300',
        className
      )}
      title={`${label}：${value}`}
    >
      <span className="text-slate-400">{icon}</span>
      <span className="text-slate-400">{label}</span>
      <span className="font-medium text-slate-100">{value}</span>
    </span>
  );
}

export function TradingSafetyBar({
  currentUserLabel,
}: {
  currentUserLabel: string;
}) {
  const { accountId, canTrade, blockedReasons } = useTradingSafety();
  const [{ data, fetching }] = useQuery({
    query: LiveSafetyStatusQuery,
    variables: { accountId },
    pause: !accountId,
    requestPolicy: 'cache-only',
  });
  const status = data?.liveSafetyStatus;
  const stateLabel = fetching
    ? '检查中'
    : canTrade
      ? 'READY'
      : status?.killSwitch
        ? 'HARD KILL'
        : 'BLOCKED';
  const summary =
    blockedReasons[0] || '协议、快照、对账、告警和备份门禁均已通过';

  return (
    <section
      aria-label="交易安全状态"
      aria-live="polite"
      className="shrink-0"
      data-testid="trading-safety-status-bar"
    >
      <StatusBar
        left={
          <>
            {canTrade ? (
              <CheckCircle2 className="h-3 w-3 shrink-0 text-emerald-400" />
            ) : status?.killSwitch ? (
              <ShieldAlert className="h-3 w-3 shrink-0 text-red-400" />
            ) : (
              <AlertTriangle className="h-3 w-3 shrink-0 text-amber-400" />
            )}
            <span
              className={cn(
                'shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] font-bold tracking-wider',
                canTrade
                  ? 'bg-emerald-400/15 text-emerald-300'
                  : 'bg-red-400/15 text-red-300'
              )}
            >
              {stateLabel}
            </span>
            <span
              className="min-w-0 truncate normal-case tracking-normal text-slate-300"
              title={summary}
            >
              {summary}
            </span>
          </>
        }
        right={
          <>
            <SafetyMetric
              icon={<RadioTower className="h-2.5 w-2.5" />}
              label="Agent"
              value={`${status?.agentMode ?? 'offline'} · ${status?.protocolVersion || '—'}`}
            />
            <SafetyMetric
              className="hidden md:inline-flex"
              icon={<TimerReset className="h-2.5 w-2.5" />}
              label="快照"
              value={ageLabel(status?.snapshotAt)}
            />
            <SafetyMetric
              className="hidden xl:inline-flex"
              icon={<DatabaseBackup className="h-2.5 w-2.5" />}
              label="备份"
              value={ageLabel(status?.lastBackupAt)}
            />
            <SafetyMetric
              className="hidden lg:inline-flex"
              icon={<ShieldAlert className="h-2.5 w-2.5" />}
              label="死信/告警"
              value={`${status?.deadLetterCount ?? 0}/${status?.unresolvedCriticalAlertCount ?? 0}`}
            />
            <span className="hidden font-mono text-[10px] text-slate-400 2xl:inline">
              {accountId || 'NO ACCOUNT'}
            </span>
            <span className="hidden text-slate-700 sm:inline">|</span>
            <span
              className="hidden min-w-0 items-center gap-1 normal-case tracking-normal text-slate-400 sm:inline-flex"
              data-testid="studio-current-user"
              title={`当前用户：${currentUserLabel}`}
            >
              <UserRound className="h-2.5 w-2.5 shrink-0" />
              <span className="max-w-28 truncate">{currentUserLabel}</span>
            </span>
          </>
        }
      />
    </section>
  );
}
