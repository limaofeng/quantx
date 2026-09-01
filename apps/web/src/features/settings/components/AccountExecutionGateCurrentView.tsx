import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Eye,
  EyeOff,
  Filter,
} from 'lucide-react';
import { useMemo, useState } from 'react';

import {
  AccountExecutionSafetyCheckStatus,
  type TradingSafety_AccountExecutionSafetyQuery,
} from '@/generated/gql/graphql';
import { cn } from '@/utils/cn';

import {
  getAccountExecutionGatePresentation,
  getBackupFreshness,
  getSnapshotFreshness,
  type AccountExecutionGateFreshness,
} from './accountExecutionGatePresentation';

type AccountExecutionSafety =
  TradingSafety_AccountExecutionSafetyQuery['accountExecutionSafety'];
type AccountExecutionCheck = AccountExecutionSafety['checks'][number];
type GateTone = 'success' | 'standby' | 'warning' | 'danger';

interface GateGroup {
  id: string;
  label: string;
  codes: readonly string[];
}

interface PresentedGate {
  check: AccountExecutionCheck;
  description: string;
  freshness: AccountExecutionGateFreshness | null;
  label: string;
  statusLabel: string;
  tone: GateTone;
}

const gateGroups: readonly GateGroup[] = [
  {
    id: 'runtime',
    label: '运行与行情链路',
    codes: [
      'SERVER_REAL_TRADING_ENABLED',
      'ACCOUNT_ALLOWLISTED',
      'ENGINE_READY',
      'LIVE_AGENT_READY',
      'AGENT_MODE_LIVE',
      'MARKET_STREAM_READY',
      'PROTOCOL_1_1',
    ],
  },
  {
    id: 'facts',
    label: '账户事实与数据时效',
    codes: [
      'SNAPSHOT_RECONCILED',
      'SNAPSHOT_FRESH',
      'SNAPSHOT_ACTIVITY_CLASSIFIED',
      'RECENT_BACKUP',
    ],
  },
  {
    id: 'controls',
    label: '执行与风险控制',
    codes: [
      'EXECUTION_CONTROL_CONFIGURED',
      'NO_CRITICAL_ALERTS',
      'NO_DEAD_LETTERS',
      'CONTROLLED_WINDOW_ACTIVE',
      'NO_EXTERNAL_BROKER_ACTIVITY',
      'KILL_SWITCH_CLEAR',
      'ACCOUNT_RISK_INCREASE_AUTHORIZED',
    ],
  },
];

const toneClasses: Record<
  GateTone,
  { badge: string; border: string; icon: string; text: string }
> = {
  success: {
    badge: 'border-emerald-400/15 bg-emerald-400/10 text-emerald-300',
    border: 'border-slate-700/50 bg-slate-900/30',
    icon: 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300',
    text: 'text-slate-400',
  },
  standby: {
    badge: 'border-primary/25 bg-primary/10 text-blue-200',
    border: 'border-primary/30 bg-primary/5',
    icon: 'border-primary/30 bg-primary/10 text-primary',
    text: 'text-blue-200/80',
  },
  warning: {
    badge: 'border-warning/25 bg-warning/10 text-warning',
    border: 'border-warning/30 bg-warning/5',
    icon: 'border-warning/30 bg-warning/10 text-warning',
    text: 'text-amber-200/80',
  },
  danger: {
    badge: 'border-rose-400/25 bg-rose-400/10 text-rose-300',
    border: 'border-rose-400/30 bg-rose-400/5',
    icon: 'border-rose-400/30 bg-rose-400/10 text-rose-300',
    text: 'text-rose-200',
  },
};

function getFreshness(
  check: AccountExecutionCheck,
  safety: AccountExecutionSafety,
  now: number
) {
  if (check.code === 'SNAPSHOT_FRESH') {
    return getSnapshotFreshness(
      safety.reconciliationAgeSeconds,
      safety.checkedAt,
      now
    );
  }
  if (check.code === 'RECENT_BACKUP') {
    return getBackupFreshness(safety.lastBackupAt, now);
  }
  return null;
}

function presentGate(
  check: AccountExecutionCheck,
  safety: AccountExecutionSafety,
  now: number
): PresentedGate {
  const presentation = getAccountExecutionGatePresentation(check.code);
  const freshness = getFreshness(check, safety, now);
  const failed = check.status === AccountExecutionSafetyCheckStatus.Failed;
  const standby = check.status === AccountExecutionSafetyCheckStatus.Standby;
  const transient =
    check.status === AccountExecutionSafetyCheckStatus.Transient;
  const tone: GateTone = failed
    ? 'danger'
    : transient
      ? 'warning'
      : standby
        ? 'standby'
        : freshness?.tone === 'expired'
          ? 'danger'
          : freshness?.tone === 'warning'
            ? 'warning'
            : 'success';
  const statusLabel = failed
    ? '需处理'
    : transient
      ? '同步中'
      : standby
        ? '休市待机'
        : freshness?.tone === 'expired'
          ? '已过期'
          : freshness?.tone === 'warning'
            ? '即将过期'
            : '通过';

  return {
    check,
    description:
      failed || transient || standby
        ? check.message
        : presentation.passedDescription,
    freshness,
    label: presentation.label,
    statusLabel,
    tone,
  };
}

function formatCheckedTime(value: string | null | undefined) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value));
}

function formatCountSummary(gates: readonly PresentedGate[]) {
  const passed = gates.filter(
    gate => gate.check.status === AccountExecutionSafetyCheckStatus.Passed
  ).length;
  const standby = gates.filter(
    gate => gate.check.status === AccountExecutionSafetyCheckStatus.Standby
  ).length;
  const transient = gates.filter(
    gate => gate.check.status === AccountExecutionSafetyCheckStatus.Transient
  ).length;
  const failed = gates.filter(
    gate => gate.check.status === AccountExecutionSafetyCheckStatus.Failed
  ).length;
  return [
    passed ? `${passed} 通过` : null,
    standby ? `${standby} 待机` : null,
    transient ? `${transient} 同步中` : null,
    failed ? `${failed} 异常` : null,
  ]
    .filter(Boolean)
    .join(' · ');
}

function GateIcon({ tone }: { tone: GateTone }) {
  const Icon =
    tone === 'success'
      ? CheckCircle2
      : tone === 'standby' || tone === 'warning'
        ? Clock3
        : AlertTriangle;
  return <Icon className="h-3.5 w-3.5" aria-hidden="true" />;
}

function FreshnessValue({
  freshness,
}: {
  freshness: AccountExecutionGateFreshness;
}) {
  const compactLabel = freshness.countdownLabel.replace('距过期 ', '剩 ');
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center gap-1 font-mono text-ui-caption font-medium tabular-nums',
        freshness.tone === 'fresh'
          ? 'text-emerald-300'
          : freshness.tone === 'warning'
            ? 'text-warning'
            : 'text-rose-300'
      )}
      aria-label={`新鲜度：${freshness.countdownLabel}`}
    >
      <Clock3 className="h-3 w-3" aria-hidden="true" />
      {compactLabel}
    </span>
  );
}

function AttentionGate({ gate }: { gate: PresentedGate }) {
  return (
    <article
      data-execution-gate={gate.check.code}
      data-gate-tone={gate.tone}
      aria-label={`${gate.label}：${gate.statusLabel}`}
      className={cn(
        'flex min-h-14 items-start gap-3 rounded-lg border px-3 py-2.5',
        toneClasses[gate.tone].border
      )}
    >
      <span
        className={cn(
          'mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border',
          toneClasses[gate.tone].icon
        )}
      >
        <GateIcon tone={gate.tone} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className="text-ui-body font-medium text-slate-100">
            {gate.label}
          </h4>
          <span
            className={cn(
              'rounded-md border px-1.5 py-0.5 text-ui-caption font-medium',
              toneClasses[gate.tone].badge
            )}
          >
            {gate.statusLabel}
          </span>
        </div>
        <div className="mt-1 flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
          <p
            className={cn(
              'min-w-0 text-ui-label leading-5',
              toneClasses[gate.tone].text
            )}
          >
            {gate.description}
          </p>
          {gate.freshness && <FreshnessValue freshness={gate.freshness} />}
        </div>
      </div>
    </article>
  );
}

function GateGroupPanel({
  expandedCode,
  gates,
  group,
  onToggle,
  showCodes,
}: {
  expandedCode: string | null;
  gates: readonly PresentedGate[];
  group: GateGroup;
  onToggle: (code: string) => void;
  showCodes: boolean;
}) {
  return (
    <section className="min-w-0 rounded-lg border border-slate-700/50 bg-slate-950/20">
      <div className="flex min-h-10 items-center justify-between gap-2 border-b border-slate-700/50 px-3 py-2">
        <h3 className="text-ui-body font-medium text-slate-100">
          {group.label}
        </h3>
        <span className="shrink-0 text-ui-caption text-slate-500">
          {formatCountSummary(gates) || '暂无检查'}
        </span>
      </div>
      <ul className="divide-y divide-border">
        {gates.map(gate => {
          const expanded = expandedCode === gate.check.code;
          return (
            <li key={gate.check.code}>
              <button
                type="button"
                aria-expanded={expanded}
                aria-controls={`gate-detail-${gate.check.code}`}
                onClick={() => onToggle(gate.check.code)}
                className="flex min-h-10 w-full cursor-pointer items-center gap-2 px-3 py-2 text-left transition-colors duration-150 hover:bg-slate-800/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/70"
              >
                <span
                  className={cn(
                    gate.tone === 'success'
                      ? 'text-emerald-300'
                      : gate.tone === 'standby'
                        ? 'text-blue-200'
                        : gate.tone === 'warning'
                          ? 'text-warning'
                          : 'text-rose-300'
                  )}
                >
                  <GateIcon tone={gate.tone} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-ui-label font-medium text-slate-200">
                    {gate.label}
                  </span>
                  {showCodes && (
                    <code className="block truncate text-ui-caption text-slate-600">
                      {gate.check.code}
                    </code>
                  )}
                </span>
                {gate.freshness && (
                  <FreshnessValue freshness={gate.freshness} />
                )}
                <span
                  className={cn(
                    'shrink-0 text-ui-caption font-medium',
                    gate.tone === 'success'
                      ? 'text-emerald-300'
                      : gate.tone === 'standby'
                        ? 'text-blue-200'
                        : gate.tone === 'warning'
                          ? 'text-warning'
                          : 'text-rose-300'
                  )}
                >
                  {gate.statusLabel}
                </span>
                <ChevronDown
                  className={cn(
                    'h-3.5 w-3.5 shrink-0 text-slate-600 transition-transform duration-150 motion-reduce:transition-none',
                    expanded && 'rotate-180'
                  )}
                  aria-hidden="true"
                />
              </button>
              {expanded && (
                <div
                  id={`gate-detail-${gate.check.code}`}
                  className={cn(
                    'border-t border-slate-800/70 px-3 py-2 text-ui-label leading-5',
                    toneClasses[gate.tone].text
                  )}
                >
                  {gate.description}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function AccountExecutionGateCurrentView({
  now,
  safety,
}: {
  now: number;
  safety: AccountExecutionSafety | null;
}) {
  const [expandedCode, setExpandedCode] = useState<string | null>(null);
  const [onlyAttention, setOnlyAttention] = useState(false);
  const [showCodes, setShowCodes] = useState(false);
  const gates = useMemo(
    () => safety?.checks.map(check => presentGate(check, safety, now)) ?? [],
    [now, safety]
  );

  if (!safety || gates.length === 0) {
    return (
      <div
        role="status"
        className="mt-4 rounded-lg border border-slate-700/50 bg-slate-900/30 px-3 py-3 text-ui-label text-slate-400"
      >
        {safety ? '尚未取得账户准入检查项。' : '正在取得账户准入判定…'}
      </div>
    );
  }

  const attentionGates = gates.filter(gate => gate.tone !== 'success');
  const failedCount = gates.filter(
    gate => gate.check.status === AccountExecutionSafetyCheckStatus.Failed
  ).length;
  const standbyCount = gates.filter(
    gate => gate.check.status === AccountExecutionSafetyCheckStatus.Standby
  ).length;
  const transientCount = gates.filter(
    gate => gate.check.status === AccountExecutionSafetyCheckStatus.Transient
  ).length;
  const passedCount = gates.filter(
    gate => gate.check.status === AccountExecutionSafetyCheckStatus.Passed
  ).length;
  const freshnessWarnings = attentionGates.filter(
    gate => gate.check.status === AccountExecutionSafetyCheckStatus.Passed
  ).length;
  const summaryTone: GateTone = failedCount
    ? 'danger'
    : transientCount
      ? 'warning'
      : standbyCount
        ? 'standby'
        : freshnessWarnings
          ? 'warning'
          : 'success';
  const summaryHeadline = failedCount
    ? `准入检查存在 ${failedCount} 项异常`
    : transientCount
      ? `行情链路有 ${transientCount} 项正在同步`
      : standbyCount
        ? '准入链路正常，当前休市待机'
        : freshnessWarnings
          ? `${freshnessWarnings} 项时效即将到期`
          : '账户实盘准入检查全部通过';
  const summaryCounts = [
    `${passedCount} 项通过`,
    standbyCount ? `${standbyCount} 项待机` : null,
    transientCount ? `${transientCount} 项同步中` : null,
    failedCount ? `${failedCount} 项异常` : null,
  ]
    .filter(Boolean)
    .join(' · ');
  const knownCodes = new Set(gateGroups.flatMap(group => group.codes));
  const groups = [
    ...gateGroups,
    ...(gates.some(gate => !knownCodes.has(gate.check.code))
      ? [
          {
            id: 'other',
            label: '其他检查',
            codes: gates
              .filter(gate => !knownCodes.has(gate.check.code))
              .map(gate => gate.check.code),
          },
        ]
      : []),
  ];

  return (
    <div className="mt-4 space-y-3">
      <div
        className={cn(
          'flex flex-wrap items-start justify-between gap-3 rounded-lg border px-3 py-3',
          toneClasses[summaryTone].border
        )}
      >
        <div className="flex min-w-0 items-start gap-3">
          <span
            className={cn(
              'flex h-9 w-9 shrink-0 items-center justify-center rounded-full border',
              toneClasses[summaryTone].icon
            )}
          >
            <GateIcon tone={summaryTone} />
          </span>
          <div className="min-w-0">
            <h3
              className={cn(
                'text-ui-title font-semibold',
                summaryTone === 'success'
                  ? 'text-emerald-200'
                  : summaryTone === 'standby'
                    ? 'text-blue-100'
                    : summaryTone === 'warning'
                      ? 'text-amber-100'
                      : 'text-rose-100'
              )}
            >
              {summaryHeadline}
            </h3>
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-ui-label text-slate-400">
              <span>{summaryCounts}</span>
              <span className="font-mono tabular-nums">
                最近检查 {formatCheckedTime(safety?.checkedAt)}
              </span>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            aria-pressed={onlyAttention}
            onClick={() => setOnlyAttention(current => !current)}
            className={cn(
              'inline-flex min-h-8 cursor-pointer items-center gap-1.5 rounded-md border px-2.5 py-1 text-ui-label transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70',
              onlyAttention
                ? 'border-primary/40 bg-primary/15 text-blue-100'
                : 'border-border text-slate-400 hover:border-primary/40 hover:text-slate-200'
            )}
          >
            <Filter className="h-3.5 w-3.5" aria-hidden="true" />
            只看需关注
          </button>
          <button
            type="button"
            aria-pressed={showCodes}
            onClick={() => setShowCodes(current => !current)}
            className="inline-flex min-h-8 cursor-pointer items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-ui-label text-slate-400 transition-colors hover:border-primary/40 hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70"
          >
            {showCodes ? (
              <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <Eye className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {showCodes ? '隐藏技术标识' : '显示技术标识'}
          </button>
        </div>
      </div>

      {attentionGates.length > 0 && (
        <section aria-labelledby="execution-gate-attention-title">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h3
              id="execution-gate-attention-title"
              className="text-ui-label font-medium text-slate-300"
            >
              需关注事项
            </h3>
            <span className="text-ui-caption text-slate-500">
              异常优先，待机其次
            </span>
          </div>
          <div className="space-y-2">
            {attentionGates.map(gate => (
              <AttentionGate key={gate.check.code} gate={gate} />
            ))}
          </div>
        </section>
      )}

      <div
        data-testid="account-execution-gates"
        className="grid items-start gap-2 md:grid-cols-2 xl:grid-cols-3"
      >
        {groups.map(group => {
          const groupGates = gates.filter(gate =>
            group.codes.includes(gate.check.code)
          );
          const visibleGates = onlyAttention
            ? groupGates.filter(gate => gate.tone !== 'success')
            : groupGates;
          if (onlyAttention && visibleGates.length === 0) return null;
          return (
            <GateGroupPanel
              key={group.id}
              expandedCode={expandedCode}
              gates={visibleGates}
              group={group}
              onToggle={code =>
                setExpandedCode(current => (current === code ? null : code))
              }
              showCodes={showCodes}
            />
          );
        })}
      </div>

      {onlyAttention && attentionGates.length === 0 && (
        <div className="rounded-lg border border-emerald-400/15 bg-emerald-400/5 px-3 py-3 text-ui-label text-emerald-200">
          当前没有需要关注的准入检查。
        </div>
      )}

      {standbyCount > 0 && failedCount === 0 && (
        <p className="flex items-center gap-1.5 text-ui-label text-blue-200/80">
          <Clock3 className="h-3.5 w-3.5" aria-hidden="true" />
          休市待机属于预期状态，不计入异常。
        </p>
      )}
      {transientCount > 0 && failedCount === 0 && (
        <p className="flex items-center gap-1.5 text-ui-label text-amber-200/80">
          <Clock3 className="h-3.5 w-3.5" aria-hidden="true" />
          同步追赶期间继续阻止增仓，但不形成异常事件。
        </p>
      )}
    </div>
  );
}
