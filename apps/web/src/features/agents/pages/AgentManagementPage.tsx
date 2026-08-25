import {
  Activity,
  AlertTriangle,
  Cable,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleDashed,
  Clipboard,
  Database,
  HardDrive,
  HeartPulse,
  KeyRound,
  LoaderCircle,
  Network,
  Radio,
  RefreshCw,
  Repeat2,
  Server,
  ShieldAlert,
  ShieldCheck,
  ShieldX,
  TimerReset,
  Unplug,
  WalletCards,
  type LucideIcon,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from 'urql';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { useAppDialog } from '@/components/ui/app-dialog-context';
import { Button } from '@/components/ui/button';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import {
  StudioPageFrame,
  StudioPageStack,
} from '@/components/ui/studio-layout';
import { gql } from '@/generated/gql';
import type {
  AgentManagement_CancelHandoverMutation,
  AgentManagement_ConnectionQuery,
  AgentManagement_CreateEnrollmentMutation,
  AgentManagement_RevokeMutation,
} from '@/generated/gql/graphql';
import { toast } from '@/hooks';
import { cn } from '@/utils/cn';

import {
  connectionHealth,
  formatBytes,
  formatDuration,
  type ConnectionTone,
} from './agentConnectionModel';

const AgentConnectionQuery = gql(`
  query AgentManagement_Connection {
    qmtAgentConnection {
      handoverStatus
      handoverDeviceStatus
      pendingEnrollmentExpiresAt
      current {
        id
        name
        status
        accountId
        mode
        websocketStatus
        xtdataStatus
        xtdataReason
        xttradingStatus
        xttradingReason
        reconciliationStatus
        lastSeenAt
        heartbeatAgeSeconds
        marketStream {
          status
          sequence
          queueDepth
          resyncs
          ackLatencyMs
          instrumentCount
          universeCount
          snapshotAgeSeconds
          commitPhase
        }
        diagnostics {
          agentVersion
          protocolVersion
          journalIntegrity
          journalSizeBytes
          journalPendingReports
          journalProcessingCommands
        }
      }
      history {
        id
        name
        status
        lastSeenAt
        revokedAt
      }
    }
  }
`);

const CreateEnrollmentMutation = gql(`
  mutation AgentManagement_CreateEnrollment($name: String!) {
    createAgentEnrollment(name: $name) {
      enrollmentCode
      expiresAt
    }
  }
`);

const CancelHandoverMutation = gql(`
  mutation AgentManagement_CancelHandover {
    cancelAgentHandover {
      success
      message
    }
  }
`);

const RevokeDeviceMutation = gql(`
  mutation AgentManagement_Revoke($deviceId: String!) {
    revokeAgentDevice(deviceId: $deviceId) {
      success
      message
      deviceId
    }
  }
`);

type CurrentConnection = NonNullable<
  AgentManagement_ConnectionQuery['qmtAgentConnection']['current']
>;

const toneClass: Record<ConnectionTone, string> = {
  ready: 'border-emerald-400/25 bg-emerald-400/10 text-emerald-300',
  degraded: 'border-amber-400/25 bg-amber-400/10 text-amber-300',
  offline: 'border-slate-500/25 bg-slate-500/10 text-slate-300',
};

function formatTimestamp(value?: string | null) {
  if (!value) return '尚未连接';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value));
}

function normalizedStatus(value: string) {
  const labels: Record<string, string> = {
    CONNECTED: '已连接',
    DISCONNECTED: '未连接',
    DISABLED: '已禁用',
    IDLE: '空闲',
    OFFLINE: '离线',
    ONLINE: '在线',
    OK: '正常',
    READY: '就绪',
    RECONCILING: '对账中',
    RECONCILE_REQUIRED: '等待对账',
    REVOKED: '已撤销',
    UNKNOWN: '未知',
  };
  const normalized = value.toUpperCase();
  return labels[normalized] ?? normalized;
}

function stageAppearance(status: string, disabledIsReady = false) {
  const normalized = status.toUpperCase();
  if (
    normalized === 'READY' ||
    normalized === 'CONNECTED' ||
    (disabledIsReady && normalized === 'DISABLED')
  ) {
    return {
      icon: CheckCircle2,
      className: 'border-emerald-400/25 bg-emerald-400/10 text-emerald-300',
    };
  }
  if (
    normalized === 'RECONCILING' ||
    normalized === 'RECONCILE_REQUIRED' ||
    normalized === 'ONLINE'
  ) {
    return {
      icon: CircleDashed,
      className: 'border-amber-400/25 bg-amber-400/10 text-amber-300',
    };
  }
  return {
    icon: Unplug,
    className: 'border-rose-400/25 bg-rose-400/10 text-rose-300',
  };
}

function ConnectionStage({
  icon: Icon,
  label,
  status,
  description,
  disabledIsReady = false,
}: {
  icon: LucideIcon;
  label: string;
  status: string;
  description: string;
  disabledIsReady?: boolean;
}) {
  const appearance = stageAppearance(status, disabledIsReady);
  const StatusIcon = appearance.icon;
  return (
    <li className="min-w-0 rounded-panel border border-white/[0.08] bg-[#0a1423]/80 p-ui-section">
      <div className="flex items-center justify-between gap-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-cyan-400/15 bg-cyan-400/[0.07] text-cyan-300">
          <Icon className="h-4 w-4" />
        </span>
        <span
          className={cn(
            'inline-flex items-center gap-1 rounded-full border px-2 py-1 text-ui-caption font-semibold',
            appearance.className
          )}
        >
          <StatusIcon className="h-3 w-3" />
          {normalizedStatus(status)}
        </span>
      </div>
      <p className="mt-4 text-ui-body font-medium text-slate-100">{label}</p>
      <p className="mt-1 text-ui-caption leading-4 text-slate-500">
        {description}
      </p>
    </li>
  );
}

function MetricCard({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="rounded-panel border border-white/[0.08] bg-[#0a1423]/70 p-ui-section">
      <p className="text-ui-caption font-medium uppercase tracking-[0.12em] text-slate-500">
        {label}
      </p>
      <p className="mt-2 font-mono text-ui-page-title font-semibold tabular-nums text-slate-100">
        {value}
      </p>
      <p className="mt-1 text-ui-caption text-slate-500">{detail}</p>
    </div>
  );
}

function DetailValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 items-center justify-between gap-ui-section border-b border-white/[0.06] py-2.5 last:border-0">
      <dt className="text-ui-label text-slate-500">{label}</dt>
      <dd className="truncate text-right font-mono text-ui-label text-slate-300">
        {value}
      </dd>
    </div>
  );
}

function ConnectionSkeleton() {
  return (
    <div className="space-y-ui-section" aria-label="正在读取 QMT 连接状态">
      <Skeleton className="h-40 w-full" />
      <div className="grid gap-3 lg:grid-cols-5">
        {Array.from({ length: 5 }).map((_, index) => (
          <Skeleton key={index} className="h-36" />
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, index) => (
          <Skeleton key={index} className="h-28" />
        ))}
      </div>
    </div>
  );
}

function HandoverProgress({
  status,
  complete,
}: {
  status: string;
  complete: boolean;
}) {
  const activeStep = complete
    ? 4
    : status === 'RECONCILING' || status === 'WAITING_FOR_READY'
      ? 3
      : status === 'WAITING_FOR_CONNECTION'
        ? 2
        : status === 'WAITING_FOR_ENROLLMENT'
          ? 1
          : 0;
  const steps = ['创建登记码', '本机登记', '建立连接', '完成对账'];
  return (
    <ol className="grid grid-cols-4 gap-2" aria-label="安全交接进度">
      {steps.map((step, index) => {
        const stepNumber = index + 1;
        const done = activeStep > stepNumber || complete;
        const active = activeStep === stepNumber;
        return (
          <li key={step} className="min-w-0">
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  'flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-ui-caption font-semibold',
                  done
                    ? 'border-emerald-400/30 bg-emerald-400/15 text-emerald-300'
                    : active
                      ? 'border-cyan-400/40 bg-cyan-400/15 text-cyan-200'
                      : 'border-white/10 bg-white/[0.03] text-slate-600'
                )}
              >
                {done ? <Check className="h-3 w-3" /> : stepNumber}
              </span>
              {index < steps.length - 1 && (
                <span
                  className={cn(
                    'h-px min-w-0 flex-1',
                    activeStep > stepNumber
                      ? 'bg-emerald-400/40'
                      : 'bg-white/10'
                  )}
                />
              )}
            </div>
            <p
              className={cn(
                'mt-2 truncate text-ui-caption',
                done || active ? 'text-slate-300' : 'text-slate-600'
              )}
            >
              {step}
            </p>
          </li>
        );
      })}
    </ol>
  );
}

function HandoverDialog({
  open,
  onOpenChange,
  connection,
  current,
  onRefresh,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connection: AgentManagement_ConnectionQuery['qmtAgentConnection'];
  current: CurrentConnection | null | undefined;
  onRefresh: () => void;
}) {
  const [name, setName] = useState('本机 QMT Agent');
  const [enrollment, setEnrollment] = useState<{
    code: string;
    expiresAt: string;
  } | null>(null);
  const [startingDeviceId, setStartingDeviceId] = useState<string | null>(null);
  const [complete, setComplete] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [createResult, createEnrollment] =
    useMutation<AgentManagement_CreateEnrollmentMutation>(
      CreateEnrollmentMutation
    );
  const [cancelResult, cancelHandover] =
    useMutation<AgentManagement_CancelHandoverMutation>(CancelHandoverMutation);

  useEffect(() => {
    if (!open) return;
    setStartingDeviceId(value => value ?? current?.id ?? null);
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [current?.id, open]);

  useEffect(() => {
    if (!open || !current || current.status !== 'READY') return;
    if (
      (startingDeviceId && current.id !== startingDeviceId) ||
      (startingDeviceId === null && enrollment)
    ) {
      setComplete(true);
    }
  }, [current, enrollment, open, startingDeviceId]);

  const expiresAt =
    enrollment?.expiresAt ?? connection.pendingEnrollmentExpiresAt;
  const remainingSeconds = expiresAt
    ? Math.max(0, Math.floor((new Date(expiresAt).getTime() - now) / 1000))
    : null;
  const hasPending =
    Boolean(enrollment) || connection.handoverStatus !== 'IDLE';

  const handleCreate = async () => {
    setStartingDeviceId(current?.id ?? null);
    setComplete(false);
    const result = await createEnrollment({
      name: name.trim() || '本机 QMT Agent',
    });
    const value = result.data?.createAgentEnrollment;
    if (!value) return;
    setEnrollment({
      code: value.enrollmentCode,
      expiresAt: value.expiresAt,
    });
    onRefresh();
  };

  const handleCancel = async () => {
    const result = await cancelHandover({});
    if (!result.data?.cancelAgentHandover.success) return;
    setEnrollment(null);
    setStartingDeviceId(null);
    setComplete(false);
    onRefresh();
    onOpenChange(false);
    toast({
      title: '安全交接已取消',
      description: '当前 Agent 保持原连接与授权。',
    });
  };

  const handleOpenChange = (nextOpen: boolean) => {
    if (nextOpen) {
      onOpenChange(true);
      return;
    }
    if (hasPending && !complete) return;
    setEnrollment(null);
    setStartingDeviceId(null);
    setComplete(false);
    onOpenChange(false);
  };

  const command = enrollment
    ? `quantx-qmt-agent enroll --code ${enrollment.code}`
    : '';

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto border-cyan-400/20 bg-[#0b1422] text-slate-100 sm:max-w-2xl">
        <DialogHeader>
          <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-panel border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
            <Repeat2 className="h-5 w-5" />
          </div>
          <DialogTitle>
            {current ? '安全更换 QMT Agent' : '登记 QMT Agent'}
          </DialogTitle>
          <DialogDescription className="leading-6 text-slate-400">
            {current
              ? '当前 Agent 会持续工作。只有新 Agent 建立连接并完成账户对账后，QuantX 才会自动撤销旧凭据。'
              : '登记码只显示一次；设备密钥仅保存在本机 Windows Credential Manager。'}
          </DialogDescription>
        </DialogHeader>

        <HandoverProgress
          status={connection.handoverStatus}
          complete={complete}
        />

        {complete ? (
          <div className="rounded-panel border border-emerald-400/25 bg-emerald-400/10 p-ui-section">
            <div className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 h-5 w-5 text-emerald-300" />
              <div>
                <p className="font-medium text-emerald-200">安全交接已完成</p>
                <p className="mt-1 text-ui-body leading-6 text-slate-400">
                  新 Agent 已达到 READY，旧设备凭据已自动撤销。
                </p>
              </div>
            </div>
          </div>
        ) : enrollment ? (
          <div className="space-y-ui-section rounded-panel border border-cyan-400/20 bg-cyan-400/[0.06] p-ui-section">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-ui-body font-medium text-cyan-100">
                  一次性登记码
                </p>
                <p className="mt-1 text-ui-label text-slate-500">
                  剩余 {formatDuration(remainingSeconds)}，过期后需重新生成
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  void navigator.clipboard.writeText(enrollment.code);
                  toast({ title: '登记码已复制' });
                }}
              >
                <Clipboard className="h-4 w-4" />
                复制登记码
              </Button>
            </div>
            <code className="block break-all rounded-lg border border-white/10 bg-[#07101c] p-3 font-mono text-ui-label leading-5 text-cyan-200">
              {enrollment.code}
            </code>
            <div>
              <p className="text-ui-label font-medium text-slate-300">
                在运行 MiniQMT 的电脑上执行
              </p>
              <div className="mt-2 flex items-center gap-2 rounded-lg border border-white/10 bg-[#07101c] p-3">
                <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap font-mono text-ui-label text-slate-300">
                  {command}
                </code>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="复制登记命令"
                  onClick={() => {
                    void navigator.clipboard.writeText(command);
                    toast({ title: '登记命令已复制' });
                  }}
                >
                  <Clipboard className="h-4 w-4" />
                </Button>
              </div>
            </div>
            <div className="flex items-center gap-2 text-ui-label text-slate-400">
              <LoaderCircle className="h-3.5 w-3.5 animate-spin text-cyan-300" />
              {connection.handoverStatus === 'RECONCILING'
                ? '新 Agent 已连接，正在等待完整账户快照对账…'
                : connection.handoverStatus === 'WAITING_FOR_READY'
                  ? '新 Agent 已上线，正在等待所有运行门禁就绪…'
                  : '正在等待新 Agent 使用登记码建立连接…'}
            </div>
          </div>
        ) : hasPending ? (
          <div className="rounded-panel border border-amber-400/20 bg-amber-400/[0.06] p-ui-section">
            <p className="text-ui-body font-medium text-amber-200">
              已存在进行中的安全交接
            </p>
            <p className="mt-2 text-ui-body leading-6 text-slate-400">
              出于安全原因，已生成的登记码无法再次显示。可以取消本次交接后生成新登记码；当前
              Agent 不受影响。
            </p>
          </div>
        ) : (
          <div className="space-y-2 rounded-panel border border-white/[0.08] bg-white/[0.02] p-ui-section">
            <Label htmlFor="agent-handover-name">设备名称</Label>
            <Input
              id="agent-handover-name"
              value={name}
              onChange={event => setName(event.target.value)}
              maxLength={120}
              className="border-slate-700 bg-[#07101c]"
            />
            <p className="text-ui-label leading-5 text-slate-500">
              新设备自动绑定当前唯一账户，不会创建额外账户或并行执行实例。
            </p>
          </div>
        )}

        {(createResult.error || cancelResult.error) && (
          <Alert variant="destructive">
            <ShieldAlert className="h-4 w-4" />
            <AlertDescription>
              {createResult.error?.message || cancelResult.error?.message}
            </AlertDescription>
          </Alert>
        )}

        <DialogFooter className="gap-2 sm:space-x-0">
          {hasPending && !complete ? (
            <Button
              variant="outline"
              onClick={() => void handleCancel()}
              disabled={cancelResult.fetching}
            >
              {cancelResult.fetching && (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              )}
              取消交接
            </Button>
          ) : (
            <Button variant="outline" onClick={() => handleOpenChange(false)}>
              {complete ? '完成' : '关闭'}
            </Button>
          )}
          {!hasPending && !complete && (
            <Button
              onClick={() => void handleCreate()}
              disabled={createResult.fetching}
            >
              {createResult.fetching ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <KeyRound className="h-4 w-4" />
              )}
              创建登记码
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function AgentManagementPanel() {
  const { confirm: confirmDialog } = useAppDialog();
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [handoverOpen, setHandoverOpen] = useState(false);
  const [query, executeQuery] = useQuery<AgentManagement_ConnectionQuery>({
    query: AgentConnectionQuery,
    requestPolicy: 'cache-and-network',
  });
  const [revokeResult, revokeDevice] =
    useMutation<AgentManagement_RevokeMutation>(RevokeDeviceMutation);

  const refresh = useCallback(() => {
    executeQuery({ requestPolicy: 'network-only' });
  }, [executeQuery]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      if (document.visibilityState === 'visible') refresh();
    }, 5000);
    return () => window.clearInterval(interval);
  }, [refresh]);

  const connection = query.data?.qmtAgentConnection;
  const current = connection?.current;
  const health = useMemo(() => connectionHealth(current), [current]);

  useEffect(() => {
    if (health.tone === 'degraded') setDiagnosticsOpen(true);
  }, [health.tone]);

  const handleRevoke = async () => {
    if (!current) return;
    const confirmed = await confirmDialog({
      title: '撤销当前 QMT Agent',
      description:
        '撤销后会立即失去行情与交易授权。在重新登记并完成对账前，实盘执行将不可用。',
      confirmText: '确认撤销',
      cancelText: '保留连接',
      variant: 'destructive',
    });
    if (!confirmed) return;
    const result = await revokeDevice({ deviceId: current.id });
    if (result.data?.revokeAgentDevice.success) {
      toast({
        title: 'QMT Agent 已撤销',
        description: '该设备凭据已立即失效。',
        variant: 'destructive',
      });
      refresh();
    }
  };

  const stages = current
    ? [
        {
          icon: Server,
          label: 'QMT Agent',
          status: current.status,
          description: '本机守护进程',
        },
        {
          icon: Network,
          label: 'QuantX WebSocket',
          status: current.websocketStatus,
          description: '出站控制链路',
        },
        {
          icon: Radio,
          label: 'XTData',
          status: current.xtdataStatus,
          description: 'MiniQMT 行情',
        },
        {
          icon: Activity,
          label: 'XTTrading',
          status: current.xttradingStatus,
          description: 'MiniQMT 交易',
          disabledIsReady: current.mode === 'data-only',
        },
        {
          icon: WalletCards,
          label: '账户对账',
          status: current.reconciliationStatus,
          description: '快照与回报收敛',
        },
      ]
    : [];

  return (
    <StudioPageStack className="space-y-ui-section pb-ui-section">
      <header className="flex flex-wrap items-start justify-between gap-ui-section">
        <div>
          <p className="text-ui-caption font-semibold uppercase tracking-[0.24em] text-cyan-400">
            Local execution gateway
          </p>
          <h1 className="mt-2 text-ui-display font-semibold tracking-tight text-slate-100">
            QMT 本机连接
          </h1>
          <p className="mt-2 max-w-3xl text-ui-body leading-6 text-slate-400">
            查看唯一 QMT Agent 从 QuantX 到 MiniQMT
            的真实连接状态。券商账号、QMT 路径和设备密钥始终留在本机。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={refresh}
            disabled={query.fetching}
          >
            <RefreshCw
              className={cn('h-4 w-4', query.fetching && 'animate-spin')}
            />
            刷新
          </Button>
          <Button size="sm" onClick={() => setHandoverOpen(true)}>
            {current ? (
              <Repeat2 className="h-4 w-4" />
            ) : (
              <Cable className="h-4 w-4" />
            )}
            {current ? '更换 Agent' : '登记 Agent'}
          </Button>
        </div>
      </header>

      {query.error && (
        <Alert variant="destructive">
          <ShieldAlert className="h-4 w-4" />
          <AlertTitle>无法读取 QMT 连接状态</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
            <span>状态服务暂不可用，请稍后重试。</span>
            <Button variant="outline" size="sm" onClick={refresh}>
              重试
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {query.fetching && !connection ? (
        <ConnectionSkeleton />
      ) : (
        <>
          <section
            className={cn(
              'relative overflow-hidden rounded-panel border bg-[#0a1423]/80 p-ui-section shadow-none shadow-black/10 sm:p-ui-panel',
              health.tone === 'ready'
                ? 'border-emerald-400/20'
                : health.tone === 'degraded'
                  ? 'border-amber-400/20'
                  : 'border-white/10'
            )}
          >
            <div
              className={cn(
                'pointer-events-none absolute -right-20 -top-24 h-64 w-64 rounded-full blur-3xl',
                health.tone === 'ready'
                  ? 'bg-emerald-400/[0.08]'
                  : health.tone === 'degraded'
                    ? 'bg-amber-400/[0.08]'
                    : 'bg-slate-400/[0.05]'
              )}
            />
            <div className="relative flex flex-wrap items-start justify-between gap-ui-section">
              <div className="flex min-w-0 items-start gap-ui-section">
                <span
                  className={cn(
                    'flex h-12 w-12 shrink-0 items-center justify-center rounded-panel border',
                    toneClass[health.tone]
                  )}
                >
                  {health.tone === 'ready' ? (
                    <HeartPulse className="h-6 w-6" />
                  ) : health.tone === 'degraded' ? (
                    <AlertTriangle className="h-6 w-6" />
                  ) : (
                    <Unplug className="h-6 w-6" />
                  )}
                </span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="truncate text-ui-heading font-semibold text-slate-100">
                      {current?.name ?? '尚未登记 QMT Agent'}
                    </h2>
                    <span
                      className={cn(
                        'inline-flex rounded-full border px-2.5 py-1 text-ui-caption font-semibold',
                        toneClass[health.tone]
                      )}
                    >
                      {health.label}
                    </span>
                    {current?.mode && (
                      <span className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 font-mono text-ui-caption uppercase text-slate-400">
                        {current.mode}
                      </span>
                    )}
                  </div>
                  <p className="mt-2 text-ui-body font-medium text-slate-300">
                    {health.title}
                  </p>
                  <p className="mt-1 max-w-2xl text-ui-label leading-5 text-slate-500">
                    {health.description}
                  </p>
                </div>
              </div>
              {current && (
                <dl className="grid min-w-[250px] grid-cols-2 gap-x-5 gap-y-3 rounded-panel border border-white/[0.07] bg-black/10 p-ui-section text-ui-label">
                  <div>
                    <dt className="text-slate-600">唯一账户</dt>
                    <dd className="mt-1 font-mono text-slate-300">
                      {current.accountId}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-slate-600">心跳年龄</dt>
                    <dd className="mt-1 font-mono text-slate-300">
                      {formatDuration(current.heartbeatAgeSeconds)}
                    </dd>
                  </div>
                  <div className="col-span-2">
                    <dt className="text-slate-600">最后在线</dt>
                    <dd className="mt-1 font-mono text-slate-300">
                      {formatTimestamp(current.lastSeenAt)}
                    </dd>
                  </div>
                </dl>
              )}
            </div>
          </section>

          {current && health.tone !== 'ready' && (
            <Alert
              className={cn(
                health.tone === 'degraded'
                  ? 'border-amber-400/25 bg-amber-400/[0.07] text-amber-100'
                  : 'border-slate-400/20 bg-slate-400/[0.05] text-slate-200'
              )}
            >
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>{health.title}</AlertTitle>
              <AlertDescription className="text-slate-400">
                {health.description} 页面只展示安全状态码，不上传 MiniQMT
                路径、端口或原始异常堆栈。
              </AlertDescription>
            </Alert>
          )}

          {current ? (
            <>
              <section className="rounded-panel border border-white/[0.08] bg-slate-950/30 p-ui-section sm:p-ui-section">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="text-ui-body font-semibold text-slate-100">
                      连接链路
                    </h2>
                    <p className="mt-1 text-ui-label text-slate-500">
                      状态每 5
                      秒自动刷新；各阶段均来自服务端确认的心跳与对账事实。
                    </p>
                  </div>
                  <span className="flex items-center gap-1.5 text-ui-caption text-slate-500">
                    <TimerReset className="h-3.5 w-3.5" />
                    自动刷新
                  </span>
                </div>
                <ol className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                  {stages.map(stage => (
                    <ConnectionStage key={stage.label} {...stage} />
                  ))}
                </ol>
              </section>

              <section>
                <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
                  <div>
                    <h2 className="text-ui-body font-semibold text-slate-100">
                      实时行情数据面
                    </h2>
                    <p className="mt-1 text-ui-label text-slate-500">
                      当前状态 {normalizedStatus(current.marketStream.status)} ·
                      提交阶段 {current.marketStream.commitPhase}
                    </p>
                  </div>
                  <span className="font-mono text-ui-caption text-slate-600">
                    stream v2
                  </span>
                </div>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
                  <MetricCard
                    label="Universe"
                    value={`${current.marketStream.instrumentCount} / ${current.marketStream.universeCount}`}
                    detail="最新值 / 标的全集"
                  />
                  <MetricCard
                    label="Sequence"
                    value={current.marketStream.sequence.toLocaleString()}
                    detail="已提交序号"
                  />
                  <MetricCard
                    label="Queue"
                    value={current.marketStream.queueDepth.toLocaleString()}
                    detail="待处理帧"
                  />
                  <MetricCard
                    label="ACK"
                    value={`${Math.round(current.marketStream.ackLatencyMs)} ms`}
                    detail="最近提交耗时"
                  />
                  <MetricCard
                    label="Resync"
                    value={current.marketStream.resyncs.toLocaleString()}
                    detail="本进程重同步"
                  />
                  <MetricCard
                    label="Snapshot age"
                    value={formatDuration(
                      current.marketStream.snapshotAgeSeconds
                    )}
                    detail="服务端快照年龄"
                  />
                </div>
              </section>

              <Collapsible
                open={diagnosticsOpen}
                onOpenChange={setDiagnosticsOpen}
                className="rounded-panel border border-white/[0.08] bg-slate-950/30"
              >
                <CollapsibleTrigger asChild>
                  <button
                    type="button"
                    className="flex w-full cursor-pointer items-center justify-between gap-ui-section p-ui-section text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-400/60"
                  >
                    <span>
                      <span className="block text-ui-body font-semibold text-slate-100">
                        内部诊断
                      </span>
                      <span className="mt-1 block text-ui-label text-slate-500">
                        版本、journal 与行情提交细节
                      </span>
                    </span>
                    <ChevronDown
                      className={cn(
                        'h-4 w-4 text-slate-500 transition-transform',
                        diagnosticsOpen && 'rotate-180'
                      )}
                    />
                  </button>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <div className="grid gap-ui-section border-t border-white/[0.07] p-ui-section lg:grid-cols-3">
                    <section className="rounded-panel border border-white/[0.07] bg-[#0a1423]/70 p-ui-section">
                      <div className="flex items-center gap-2 text-ui-body font-medium text-slate-200">
                        <Server className="h-4 w-4 text-cyan-300" />
                        Agent 运行时
                      </div>
                      <dl className="mt-3">
                        <DetailValue
                          label="Agent 版本"
                          value={current.diagnostics.agentVersion || '—'}
                        />
                        <DetailValue
                          label="协议版本"
                          value={current.diagnostics.protocolVersion || '—'}
                        />
                        <DetailValue label="设备标识" value={current.id} />
                        <DetailValue
                          label="最近心跳"
                          value={formatDuration(current.heartbeatAgeSeconds)}
                        />
                      </dl>
                    </section>
                    <section className="rounded-panel border border-white/[0.07] bg-[#0a1423]/70 p-ui-section">
                      <div className="flex items-center gap-2 text-ui-body font-medium text-slate-200">
                        <HardDrive className="h-4 w-4 text-cyan-300" />
                        本地 Journal
                      </div>
                      <dl className="mt-3">
                        <DetailValue
                          label="完整性"
                          value={normalizedStatus(
                            current.diagnostics.journalIntegrity
                          )}
                        />
                        <DetailValue
                          label="文件大小"
                          value={formatBytes(
                            current.diagnostics.journalSizeBytes
                          )}
                        />
                        <DetailValue
                          label="待上报"
                          value={current.diagnostics.journalPendingReports.toLocaleString()}
                        />
                        <DetailValue
                          label="处理中命令"
                          value={current.diagnostics.journalProcessingCommands.toLocaleString()}
                        />
                      </dl>
                    </section>
                    <section className="rounded-panel border border-white/[0.07] bg-[#0a1423]/70 p-ui-section">
                      <div className="flex items-center gap-2 text-ui-body font-medium text-slate-200">
                        <Database className="h-4 w-4 text-cyan-300" />
                        行情提交
                      </div>
                      <dl className="mt-3">
                        <DetailValue
                          label="数据面状态"
                          value={normalizedStatus(current.marketStream.status)}
                        />
                        <DetailValue
                          label="提交阶段"
                          value={current.marketStream.commitPhase}
                        />
                        <DetailValue
                          label="队列深度"
                          value={current.marketStream.queueDepth.toLocaleString()}
                        />
                        <DetailValue
                          label="最近 ACK"
                          value={`${current.marketStream.ackLatencyMs.toFixed(1)} ms`}
                        />
                      </dl>
                    </section>
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-ui-section border-t border-white/[0.07] px-ui-section py-ui-section">
                    <div className="flex items-start gap-2 text-ui-label leading-5 text-slate-500">
                      <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
                      <span>
                        Web 端只读诊断状态；不提供远程启动、重连或 MiniQMT
                        控制能力。
                      </span>
                    </div>
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => void handleRevoke()}
                      disabled={revokeResult.fetching}
                    >
                      <ShieldX className="h-4 w-4" />
                      撤销当前 Agent
                    </Button>
                  </div>
                </CollapsibleContent>
              </Collapsible>
            </>
          ) : (
            <section className="rounded-panel border border-dashed border-cyan-400/20 bg-cyan-400/[0.03] px-ui-panel py-ui-empty text-center">
              <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-panel border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
                <Cable className="h-6 w-6" />
              </span>
              <h2 className="mt-4 text-ui-title font-semibold text-slate-100">
                登记当前电脑上的 QMT Agent
              </h2>
              <p className="mx-auto mt-2 max-w-lg text-ui-body leading-6 text-slate-500">
                Agent 只向 QuantX 建立出站连接。完成登记后，这里会展示
                XTData、XTTrading、账户对账与实时行情指标。
              </p>
              <Button className="mt-5" onClick={() => setHandoverOpen(true)}>
                <KeyRound className="h-4 w-4" />
                创建一次性登记码
              </Button>
            </section>
          )}

          {connection && connection.history.length > 0 && (
            <Collapsible open={historyOpen} onOpenChange={setHistoryOpen}>
              <CollapsibleTrigger asChild>
                <button
                  type="button"
                  className="flex w-full cursor-pointer items-center justify-between rounded-panel border border-white/[0.07] bg-white/[0.02] px-ui-section py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60"
                >
                  <span className="text-ui-label text-slate-500">
                    历史登记 · {connection.history.length}
                  </span>
                  <ChevronDown
                    className={cn(
                      'h-4 w-4 text-slate-600 transition-transform',
                      historyOpen && 'rotate-180'
                    )}
                  />
                </button>
              </CollapsibleTrigger>
              <CollapsibleContent className="mt-2 overflow-hidden rounded-panel border border-white/[0.07] bg-slate-950/30">
                <div className="divide-y divide-white/[0.06]">
                  {connection.history.map(device => (
                    <div
                      key={device.id}
                      className="grid gap-2 px-ui-section py-3 text-ui-label sm:grid-cols-[minmax(0,1fr)_110px_180px] sm:items-center"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-slate-300">{device.name}</p>
                        <p className="mt-1 truncate font-mono text-ui-caption text-slate-600">
                          {device.id}
                        </p>
                      </div>
                      <span className="text-slate-500">
                        {normalizedStatus(device.status)}
                      </span>
                      <span className="font-mono text-slate-600">
                        {formatTimestamp(device.revokedAt ?? device.lastSeenAt)}
                      </span>
                    </div>
                  ))}
                </div>
              </CollapsibleContent>
            </Collapsible>
          )}
        </>
      )}

      {connection && (
        <HandoverDialog
          open={handoverOpen}
          onOpenChange={setHandoverOpen}
          connection={connection}
          current={current}
          onRefresh={refresh}
        />
      )}
    </StudioPageStack>
  );
}

export function AgentManagementPage() {
  return (
    <StudioPageFrame>
      <AgentManagementPanel />
    </StudioPageFrame>
  );
}
