import {
  AlertTriangle,
  ArrowLeftRight,
  CheckCircle2,
  DatabaseBackup,
  RadioTower,
  ShieldCheck,
  ShieldAlert,
  TimerReset,
  UserRound,
} from 'lucide-react';
import type { ReactNode } from 'react';

import { StatusBar } from '@/components/studio-workbench';
import { useStudioNavigate } from '@/components/studio-workspace';
import { AccountExecutionHealthStatus } from '@/generated/gql/graphql';
import { cn } from '@/utils/cn';

import {
  accountExecutionModeLabel,
  accountHealthLabel,
  accountSafetySummary,
} from './presentation';
import { ageSecondsLabel } from './time';
import { useTradingSafety } from './trading-safety-context';

function ageLabel(value?: string | null) {
  if (!value) return '无记录';
  const milliseconds = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return '时间异常';
  return ageSecondsLabel(milliseconds / 1000);
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
        'inline-flex items-center gap-1 whitespace-nowrap text-ui-caption normal-case tracking-normal text-slate-300',
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
  const navigate = useStudioNavigate();
  const { accountId, blockedReasons, fetching, safety } = useTradingSafety();
  const loading = fetching && !safety;
  const healthStatus =
    safety?.healthStatus ?? AccountExecutionHealthStatus.Blocked;
  const healthLabel = loading ? '加载中' : accountHealthLabel(healthStatus);
  const executionLabel = accountExecutionModeLabel(safety?.executionMode);
  const summary = safety
    ? accountSafetySummary(safety)
    : blockedReasons[0] || '账户安全状态尚未加载';
  const isHealthy = healthStatus === AccountExecutionHealthStatus.Healthy;
  const isKilled = healthStatus === AccountExecutionHealthStatus.Killed;
  const canIncreaseRisk = Boolean(safety?.canIncreaseRisk);

  return (
    <section
      aria-label="交易安全状态"
      aria-live="polite"
      className="shrink-0 cursor-pointer"
      data-testid="trading-safety-status-bar"
      role="button"
      tabIndex={0}
      title="打开账户交易安全设置"
      onClick={() => navigate('/settings/trading-safety')}
      onKeyDown={event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          navigate('/settings/trading-safety');
        }
      }}
    >
      <StatusBar
        variant="workspace"
        left={
          <>
            {canIncreaseRisk ? (
              <CheckCircle2 className="h-3 w-3 shrink-0 text-emerald-400" />
            ) : isKilled ? (
              <ShieldAlert className="h-3 w-3 shrink-0 text-red-400" />
            ) : isHealthy ? (
              <ShieldCheck className="h-3 w-3 shrink-0 text-emerald-400" />
            ) : (
              <AlertTriangle className="h-3 w-3 shrink-0 text-amber-400" />
            )}
            <span
              className={cn(
                'shrink-0 rounded px-1.5 py-0.5 font-mono text-ui-caption font-bold tracking-wider',
                canIncreaseRisk
                  ? 'bg-emerald-400/15 text-emerald-300'
                  : isKilled
                    ? 'bg-red-400/15 text-red-300'
                    : isHealthy
                      ? 'bg-emerald-400/15 text-emerald-300'
                      : 'bg-amber-400/15 text-amber-200'
              )}
            >
              {healthLabel}
            </span>
            <span className="shrink-0 font-medium text-slate-200">
              交易权限：{executionLabel}
            </span>
            <span className="text-slate-700">|</span>
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
              value={`${safety?.agentMode ?? 'offline'} · ${safety?.protocolVersion || '—'}`}
            />
            <SafetyMetric
              className="hidden md:inline-flex"
              icon={<TimerReset className="h-2.5 w-2.5" />}
              label="快照"
              value={ageSecondsLabel(safety?.reconciliationAgeSeconds)}
            />
            <SafetyMetric
              className="hidden xl:inline-flex"
              icon={<DatabaseBackup className="h-2.5 w-2.5" />}
              label="备份"
              value={ageLabel(safety?.lastBackupAt)}
            />
            <SafetyMetric
              className="hidden lg:inline-flex"
              icon={<ArrowLeftRight className="h-2.5 w-2.5" />}
              label="手工委托/成交"
              value={`${safety?.externalOrderCount ?? 0}/${safety?.externalTradeCount ?? 0}`}
            />
            <SafetyMetric
              className="hidden 2xl:inline-flex"
              icon={<ShieldAlert className="h-2.5 w-2.5" />}
              label="死信/告警"
              value={`${safety?.deadLetterCount ?? 0}/${safety?.unresolvedCriticalAlertCount ?? 0}`}
            />
            <span className="hidden font-mono text-ui-caption text-slate-400 2xl:inline">
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
