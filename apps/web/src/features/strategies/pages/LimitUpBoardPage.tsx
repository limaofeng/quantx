import {
  AlertTriangle,
  BarChart3,
  Check,
  Clock3,
  Database,
  FlaskConical,
  History,
  LayoutList,
  Radar,
  RadioTower,
  RefreshCw,
  Settings2,
  ShieldCheck,
  ShieldX,
  Target,
  WalletCards,
  X,
} from 'lucide-react';
import {
  lazy,
  type ReactNode,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useLocation } from 'wouter';

import {
  StudioWorkbench,
  type StudioMode,
} from '@/components/studio-workbench';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
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
import { NativeSelect } from '@/components/ui/native-select';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from '@/components/ui/sheet';
import { useAMarketSession } from '@/features/dashboard/hooks/useAMarketSession';
import { useCurrentAccount } from '@/features/dashboard/hooks/useDashboard';
import { useToast } from '@/hooks/use-toast';
import { financialToneClass } from '@/shared/utils/financialColors';
import { cn } from '@/utils/cn';

import { LimitUpBoardHealthConsole } from '../components/LimitUpBoardHealthConsole';
import { LimitUpCandidateInspector } from '../components/LimitUpCandidateInspector';
import { LimitUpRadarPanel } from '../components/LimitUpRadarPanel';
import { deriveLimitUpBoardHealth } from '../domain/limitUpBoardHealth';
import { useLimitUpBoardAssistant } from '../hooks/useLimitUpBoardAssistant';
import { useLimitUpRadar } from '../hooks/useLimitUpRadar';

const LimitUpBoardReplayPanel = lazy(async () => {
  const module =
    await import('../components/limit-up-board-replay/LimitUpBoardReplayPanel');
  return { default: module.LimitUpBoardReplayPanel };
});

function formatMoney(value?: number | null) {
  if (!value || !Number.isFinite(value)) return '--';
  return `¥${value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
}

function useCountdown(expiresAt?: string | null) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!expiresAt) return;
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [expiresAt]);
  const expires = expiresAt ? new Date(expiresAt).getTime() : 0;
  const remaining = Number.isFinite(expires) ? Math.max(0, expires - now) : 0;
  return {
    expired: remaining <= 0,
    label: remaining > 0 ? `${Math.ceil(remaining / 1000)}s` : '已过期',
    progress: Math.min(100, (remaining / 15_000) * 100),
  };
}

function PendingSignalCard({
  busy,
  canApprove,
  intent,
  onApprove,
  onReject,
}: {
  busy: boolean;
  canApprove: boolean;
  intent: {
    approvalExpiresAt?: string | null;
    distanceToLimitTicks?: number | null;
    id: string;
    instrumentCode: string;
    limitUpPrice?: number | null;
    signalPrice?: number | null;
    targetAmount?: number | null;
    targetPositionPct?: number | null;
  };
  onApprove: () => void;
  onReject: () => void;
}) {
  const countdown = useCountdown(intent.approvalExpiresAt);
  return (
    <article className="overflow-hidden rounded-sm border border-amber-400/30 bg-amber-400/[0.07]">
      <div className="flex items-center justify-between gap-3 px-3 py-2.5">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-ui-label font-black text-slate-100">
              {intent.instrumentCode}
            </span>
            <Badge className="border-amber-400/25 bg-amber-400/10 text-ui-micro text-amber-200 hover:bg-amber-400/10">
              待确认
            </Badge>
          </div>
          <div className="mt-1 text-ui-caption text-slate-500">
            目标仓位{' '}
            {intent.targetPositionPct != null
              ? `${(intent.targetPositionPct * 100).toFixed(2)}%`
              : formatMoney(intent.targetAmount)}{' '}
            · 涨停价 {intent.limitUpPrice?.toFixed(2) ?? '--'}
          </div>
        </div>
        <div
          className={cn(
            'flex items-center gap-1 font-mono text-ui-body font-black',
            countdown.expired ? 'text-rose-300' : 'text-amber-200'
          )}
        >
          <Clock3 className="h-3.5 w-3.5" />
          {countdown.label}
        </div>
      </div>
      <div className="h-0.5 bg-white/[0.05]">
        <div
          className="h-full bg-amber-400 transition-[width] duration-200 motion-reduce:transition-none"
          style={{ width: `${countdown.progress}%` }}
        />
      </div>
      <div className="grid grid-cols-3 gap-px bg-white/[0.06] text-ui-micro">
        <SignalMetric
          label="信号价"
          value={intent.signalPrice?.toFixed(2) ?? '--'}
        />
        <SignalMetric
          label="距涨停"
          value={`${intent.distanceToLimitTicks?.toFixed(0) ?? '--'} 档`}
        />
        <SignalMetric label="价格类型" value="涨停限价" />
      </div>
      <div className="grid grid-cols-2 gap-2 p-2.5">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy || countdown.expired}
          onClick={onReject}
          className="h-control-compact border-white/10 bg-white/[0.025] text-ui-caption text-slate-300 hover:bg-white/[0.06]"
        >
          <X className="h-3.5 w-3.5" />
          忽略
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={busy || countdown.expired || !canApprove}
          onClick={onApprove}
          className="h-control-compact bg-market-buy-cta text-ui-caption font-black text-white hover:bg-market-buy-cta/90"
          title={
            !canApprove
              ? '账户执行门禁尚未通过'
              : '确认后立即重新校验行情与风控'
          }
        >
          {busy ? (
            <RefreshCw className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
          ) : (
            <Check className="h-3.5 w-3.5" />
          )}
          确认买入
        </Button>
      </div>
    </article>
  );
}

function SignalMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-[#0d1626] px-3 py-2 text-slate-500">
      {label}
      <strong className="mt-0.5 block font-mono text-ui-caption text-slate-200">
        {value}
      </strong>
    </div>
  );
}

function ExitPlanCard({
  plan,
}: {
  plan: {
    autoExitAuthorized: boolean;
    entryAvgPrice: number;
    entryTradeDate?: string | null;
    holdingTradingDays: number;
    instrumentCode: string;
    lastNetProfitPct: number;
    lastPrice: number;
    pendingOrderId?: string | null;
    remainingVolume: number;
    ruleTypes: string[];
    status: string;
  };
}) {
  const today = new Date().toLocaleDateString('en-CA');
  const waitingT1 = plan.entryTradeDate === today;
  const hasError = plan.status === 'ERROR';
  const rules = [
    plan.ruleTypes.includes('LIMIT_UP_TOUCH') ? 'T+1 触及二板全卖' : null,
    plan.ruleTypes.includes('LIMIT_UP_BREAK') ? '二板炸板全卖' : null,
    plan.ruleTypes.includes('TRAILING_PRICE_DRAWDOWN') ? '弱势回撤全卖' : null,
    plan.ruleTypes.includes('MAX_HOLDING_DAYS') ? 'T+1 14:50 清仓' : null,
  ].filter((rule): rule is string => Boolean(rule));
  return (
    <article
      className={cn(
        'rounded-sm border p-3',
        hasError
          ? 'border-rose-400/25 bg-rose-400/[0.055]'
          : 'border-emerald-400/20 bg-emerald-400/[0.045]'
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-ui-label font-black text-slate-100">
              {plan.instrumentCode}
            </span>
            <Badge
              className={cn(
                'text-ui-micro',
                hasError
                  ? 'border-rose-400/25 bg-rose-400/10 text-rose-200 hover:bg-rose-400/10'
                  : 'border-emerald-400/20 bg-emerald-400/10 text-emerald-200 hover:bg-emerald-400/10'
              )}
            >
              {hasError
                ? '计划异常'
                : plan.pendingOrderId
                  ? '退出委托中'
                  : '自动托管'}
            </Badge>
          </div>
          <div className="mt-1 text-ui-caption text-slate-500">
            剩余 {plan.remainingVolume.toLocaleString('zh-CN')} 股 · 成本{' '}
            {plan.entryAvgPrice.toFixed(2)}
          </div>
        </div>
        <div className="text-right">
          <div
            className={cn(
              'font-mono text-ui-body font-black',
              financialToneClass(plan.lastNetProfitPct, 'holding')
            )}
          >
            {plan.lastNetProfitPct >= 0 ? '+' : ''}
            {plan.lastNetProfitPct.toFixed(2)}%
          </div>
          <div className="text-ui-micro text-slate-600">
            现价 {plan.lastPrice.toFixed(2)}
          </div>
        </div>
      </div>
      <div className="mt-2 flex items-center justify-between rounded-sm border border-white/[0.05] bg-black/10 px-2.5 py-2 text-ui-caption">
        <span className="text-slate-500">T+1 状态</span>
        <span className={waitingT1 ? 'text-amber-200' : 'text-emerald-200'}>
          {waitingT1 ? '今日买入，等待可卖' : '已进入可卖日'}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {rules.map(rule => (
          <span
            key={rule}
            className="rounded-sm border border-white/[0.07] bg-white/[0.025] px-1.5 py-1 text-ui-micro text-slate-400"
          >
            {rule}
          </span>
        ))}
      </div>
      {!plan.autoExitAuthorized ? (
        <div className="mt-2 flex items-center gap-1.5 text-ui-micro text-rose-300">
          <ShieldX className="h-3 w-3" /> 自动卖出授权未生效
        </div>
      ) : null}
    </article>
  );
}

type LimitUpWorkspaceMode = 'REALTIME' | 'REPLAY';
type LimitUpRealtimeView = 'RADAR' | 'SIGNALS' | 'POSITIONS';

export default function LimitUpBoardPage() {
  const [, setLocation] = useLocation();
  const { toast } = useToast();
  const marketSession = useAMarketSession();
  const accountResult = useCurrentAccount();
  const account = accountResult.data?.currentAccount;
  const accountId = account?.id;
  const [workspaceMode, setWorkspaceMode] =
    useState<LimitUpWorkspaceMode>('REALTIME');
  const radar = useLimitUpRadar(workspaceMode === 'REALTIME', accountId);
  const assistant = useLimitUpBoardAssistant(
    accountId,
    workspaceMode === 'REALTIME'
  );
  const [activeView, setActiveView] = useState<LimitUpRealtimeView>('RADAR');
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [positionCap, setPositionCap] = useState(2);
  const [dailyExposureCap, setDailyExposureCap] = useState(6);
  const [tailLossBudget, setTailLossBudget] = useState(0.15);
  const [maxOpenPositions, setMaxOpenPositions] = useState(2);
  const [mode, setMode] = useState('paper');
  const [autoExitAcknowledged, setAutoExitAcknowledged] = useState(false);
  const candidateTriggerRef = useRef<HTMLElement | null>(null);
  const knownPendingIntentIds = useRef<Set<string> | null>(null);

  useEffect(() => {
    if (!settingsOpen) return;
    setPositionCap(
      (assistant.currentSettings.maxSinglePositionPct ?? 0.02) * 100
    );
    setDailyExposureCap(
      (assistant.currentSettings.maxDailyExposurePct ?? 0.06) * 100
    );
    setTailLossBudget(
      (assistant.currentSettings.plannedTailLossPct ?? 0.0015) * 100
    );
    setMaxOpenPositions(assistant.currentSettings.maxOpenPositions ?? 2);
    setMode(assistant.currentSettings.mode ?? 'paper');
    setAutoExitAcknowledged(
      assistant.currentSettings.autoExitAcknowledged ?? false
    );
  }, [assistant.currentSettings, settingsOpen]);

  useEffect(() => {
    const nextIds = new Set(assistant.pendingIntents.map(intent => intent.id));
    const knownIds = knownPendingIntentIds.current;
    if (
      workspaceMode === 'REALTIME' &&
      knownIds &&
      [...nextIds].some(id => !knownIds.has(id))
    ) {
      setActiveView('SIGNALS');
    }
    knownPendingIntentIds.current = nextIds;
  }, [assistant.pendingIntents, workspaceMode]);

  const armedCodes = useMemo(
    () =>
      new Set(
        radar.candidates
          .filter(item => item.candidatePreference === 'PREFER')
          .map(item => item.code)
      ),
    [radar.candidates]
  );
  const pendingCodes = useMemo(
    () => new Set(assistant.pendingIntents.map(item => item.instrumentCode)),
    [assistant.pendingIntents]
  );
  const exitPlanCodes = useMemo(
    () => new Set(assistant.exitPlans.map(item => item.instrumentCode)),
    [assistant.exitPlans]
  );
  const systemWarnings = useMemo(
    () =>
      radar.warnings.filter(
        warning => !/候选.*过期|行情已过期|雷达数据提示|仅供观察/.test(warning)
      ),
    [radar.warnings]
  );
  const enabled = Boolean(assistant.assistant?.enabled);
  const selectedCandidate = useMemo(
    () =>
      selectedCode
        ? (radar.candidates.find(
            candidate => candidate.code === selectedCode
          ) ?? null)
        : null,
    [radar.candidates, selectedCode]
  );
  const exitPlanErrorCount = assistant.exitPlans.filter(
    plan => plan.status === 'ERROR'
  ).length;
  const health = useMemo(
    () =>
      deriveLimitUpBoardHealth({
        assistant: {
          activeExitPlanCount:
            assistant.assistant?.activeExitPlanCount ??
            assistant.exitPlans.length,
          blockedReasons: [...(assistant.assistant?.blockedReasons ?? [])],
          canApprove: Boolean(assistant.assistant?.canApprove),
          enabled,
          killSwitch: Boolean(assistant.assistant?.killSwitch),
          lastError: assistant.assistant?.lastError,
          monitoredCount:
            assistant.assistant?.monitoredCount ?? armedCodes.size,
          pendingSignalCount:
            assistant.assistant?.pendingSignalCount ??
            assistant.pendingIntents.length,
          projectionGeneratedAt:
            assistant.assistant?.projectionGeneratedAt ?? null,
          projectionVersion: assistant.assistant?.projectionVersion ?? null,
          promotionModelMode:
            assistant.assistant?.promotionModelMode ??
            assistant.currentSettings.promotionModelMode,
          reconcileStatus: assistant.assistant?.reconcileStatus ?? null,
          runStatus: assistant.assistant?.runStatus ?? null,
        },
        exitPlanErrorCount,
        marketSessionPhase: marketSession.phase,
        radar: {
          scannerRunning: radar.isScannerRunning,
          staleCount: radar.summary.staleCount,
          updating: radar.fetching,
          warnings: systemWarnings,
        },
      }),
    [
      armedCodes.size,
      assistant.assistant,
      assistant.currentSettings.promotionModelMode,
      assistant.exitPlans.length,
      assistant.pendingIntents.length,
      enabled,
      exitPlanErrorCount,
      marketSession.phase,
      radar.fetching,
      radar.isScannerRunning,
      radar.summary.staleCount,
      systemWarnings,
    ]
  );

  const act = async (key: string, action: () => Promise<unknown>) => {
    if (busyAction) return;
    setBusyAction(key);
    try {
      await action();
    } catch (error) {
      toast({
        title: '操作未完成',
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      });
    } finally {
      setBusyAction(null);
    }
  };

  const saveSettings = () =>
    act('settings', async () => {
      const payload = await assistant.save({
        autoExitAcknowledged,
        maxDailyExposurePct: dailyExposureCap / 100,
        maxOpenPositions,
        maxSinglePositionPct: positionCap / 100,
        mode,
        plannedTailLossPct: tailLossBudget / 100,
      });
      setSettingsOpen(false);
      toast({ title: '设置已保存', description: payload.message });
    });

  const syncWorkbench = () =>
    act('refresh', async () => {
      radar.refresh({ requestPolicy: 'network-only' });
      await assistant.reconcile();
    });

  const toggleAssistant = (checked: boolean) =>
    act('toggle', () => assistant.save({ enabled: checked }));

  const selectCandidate = (code: string) => {
    candidateTriggerRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    setSelectedCode(code);
  };

  const healthConsoleProps = {
    accountId,
    accountName: account?.accountName,
    actionLoading: Boolean(busyAction),
    assistantEnabled: enabled,
    health,
    mode: assistant.currentSettings.mode ?? 'paper',
    onOpenSettings: () => setSettingsOpen(true),
    onRefresh: syncWorkbench,
    onToggleAssistant: toggleAssistant,
    radarUpdatedAt: radar.updatedAt,
    refreshing: busyAction === 'refresh',
  };

  const replaySidebar = (
    <aside className="studio-workspace-surface flex h-full min-h-0 flex-col">
      <div className="flex h-[68px] shrink-0 items-center border-b border-white/[0.05] px-ui-section">
        <div>
          <div className="text-ui-caption font-black uppercase tracking-[0.24em] text-cyan-300">
            Replay Lab
          </div>
          <h1 className="mt-1 text-ui-title font-black text-slate-100">
            打板回放
          </h1>
        </div>
      </div>
      <div className="border-b border-white/[0.05] p-ui-section">
        <div className="flex items-center gap-2 text-ui-label font-black text-cyan-100">
          <ShieldCheck className="h-4 w-4 text-cyan-300" />
          隔离回测环境
        </div>
        <p className="mt-2 text-ui-caption leading-5 text-slate-600">
          回放使用 BACKTEST
          Broker，测试信号自动确认；实时助手保持原状态，不会提交实盘委托。
        </p>
      </div>
      <div className="space-y-3 p-ui-section text-ui-caption text-slate-500">
        <div className="flex items-start gap-2">
          <Database className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-600" />
          按历史时点重放动态候选与原始五档行情。
        </div>
        <div className="flex items-start gap-2">
          <BarChart3 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-600" />
          同时比较确认延迟、成交量约束与账户收益曲线。
        </div>
        <div className="flex items-start gap-2">
          <History className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-600" />
          回放任务、输入指纹与数据质量均保留审计记录。
        </div>
      </div>
      <div className="mt-auto shrink-0 border-t border-white/[0.06] bg-[#091322] p-3">
        <div className="mb-1.5 text-ui-caption font-black uppercase tracking-[0.12em] text-slate-600">
          默认回放账户
        </div>
        <div className="flex h-10 items-center border border-white/[0.08] bg-white/[0.025] px-3 font-mono text-ui-label text-slate-300">
          {accountId || '未配置'}
        </div>
      </div>
    </aside>
  );

  const realtimeViews: Array<{
    count: number;
    icon: typeof RadioTower;
    id: LimitUpRealtimeView;
    label: string;
  }> = [
    {
      count: radar.summary.eligibleCount,
      icon: RadioTower,
      id: 'RADAR',
      label: '候选雷达',
    },
    {
      count: assistant.pendingIntents.length,
      icon: Clock3,
      id: 'SIGNALS',
      label: '待确认信号',
    },
    {
      count: assistant.exitPlans.length,
      icon: WalletCards,
      id: 'POSITIONS',
      label: 'T+1 持仓',
    },
  ];

  const studioModes: StudioMode[] = realtimeViews.map(
    ({ icon, id, label }) => ({
      icon,
      id,
      label,
    })
  );
  const updatedAt = radar.updatedAt
    ? new Date(radar.updatedAt).toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
    : '--';

  const toolbar = (
    <header className="studio-workspace-surface flex h-12 shrink-0 items-center justify-between gap-3 overflow-hidden border-b border-white/[0.05] px-ui-section">
      <nav
        aria-label="打板工作区"
        className="flex h-full min-w-0 items-stretch overflow-x-auto custom-scrollbar"
      >
        {(['REALTIME', 'REPLAY'] as const).map(mode => {
          const active = workspaceMode === mode;
          return (
            <button
              key={mode}
              type="button"
              aria-controls="limit-up-workbench-content"
              aria-pressed={active}
              className={cn(
                'relative flex h-full shrink-0 cursor-pointer items-center gap-1.5 px-3 text-ui-caption font-black transition-colors after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset',
                active
                  ? mode === 'REPLAY'
                    ? 'text-cyan-200 after:bg-cyan-400 focus-visible:ring-cyan-400/60'
                    : 'text-red-200 after:bg-red-400 focus-visible:ring-red-500/60'
                  : 'text-slate-600 hover:text-slate-200'
              )}
              onClick={() => setWorkspaceMode(mode)}
            >
              {mode === 'REPLAY' ? (
                <FlaskConical className="h-3.5 w-3.5" />
              ) : (
                <Radar className="h-3.5 w-3.5" />
              )}
              {mode === 'REPLAY' ? '回放测试' : '实时监控'}
            </button>
          );
        })}
        {workspaceMode === 'REALTIME' ? (
          <>
            <span className="mx-2 my-3 w-px shrink-0 bg-white/[0.08]" />
            <div
              aria-label="首板实时监控视图"
              className="flex h-full items-stretch"
              role="tablist"
            >
              {realtimeViews.map(view => {
                const active = activeView === view.id;
                return (
                  <button
                    key={view.id}
                    type="button"
                    aria-controls="limit-up-realtime-view"
                    aria-selected={active}
                    className={cn(
                      'relative h-full shrink-0 cursor-pointer px-3 text-ui-label font-bold transition-colors after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-red-500/60',
                      active
                        ? 'text-red-200 after:bg-red-400'
                        : 'text-slate-500 hover:text-slate-200'
                    )}
                    onClick={() => setActiveView(view.id)}
                    role="tab"
                  >
                    {view.label}
                    {view.count ? (
                      <span className="ml-1.5 rounded-sm bg-white/[0.07] px-1.5 py-0.5 font-mono text-ui-micro text-slate-300">
                        {view.count}
                      </span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          </>
        ) : null}
      </nav>

      <div className="flex shrink-0 items-center gap-2">
        {workspaceMode === 'REPLAY' ? (
          <span className="hidden items-center gap-1.5 text-ui-caption font-bold text-cyan-200 sm:inline-flex">
            <ShieldCheck className="h-3.5 w-3.5" />
            隔离回测 · 自动确认测试信号
          </span>
        ) : (
          <>
            <span
              className={cn(
                'hidden items-center gap-1.5 text-ui-caption font-bold md:inline-flex',
                enabled ? 'text-emerald-300' : 'text-slate-600'
              )}
            >
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  enabled
                    ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.65)]'
                    : 'bg-slate-700'
                )}
              />
              {enabled ? '晋级助手运行中' : '晋级助手已停止'}
            </span>
            <span className="hidden h-4 w-px bg-white/[0.08] sm:block" />
            <span className="hidden font-mono text-ui-micro text-slate-600 lg:inline">
              {radar.isScannerRunning ? '雷达在线' : '雷达离线'} · 更新{' '}
              {updatedAt}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="同步首板业务链"
              className="h-control-compact w-8 text-slate-500 hover:bg-white/[0.05] hover:text-slate-100"
              onClick={syncWorkbench}
              disabled={!accountId || busyAction === 'refresh'}
            >
              <RefreshCw
                className={cn(
                  'h-3.5 w-3.5',
                  busyAction === 'refresh' &&
                    'animate-spin motion-reduce:animate-none'
                )}
              />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="打开首板风险设置"
              className="h-control-compact w-8 text-slate-500 hover:bg-white/[0.05] hover:text-slate-100"
              onClick={() => setSettingsOpen(true)}
            >
              <Settings2 className="h-3.5 w-3.5" />
            </Button>
          </>
        )}
      </div>
    </header>
  );

  const content = (
    <div
      className="studio-workspace-surface flex h-full min-h-0 w-full flex-col overflow-hidden text-slate-100"
      data-testid="limit-up-board-page"
    >
      {toolbar}

      {workspaceMode === 'REALTIME' && assistant.assistant?.lastError ? (
        <div
          role="alert"
          className="flex shrink-0 items-start gap-2 border-b border-rose-400/20 bg-rose-500/[0.08] px-3 py-2 text-ui-caption text-rose-100"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <strong>助手受保护</strong>
          <span className="truncate text-rose-100/75">
            {assistant.assistant.lastError}
          </span>
        </div>
      ) : null}

      <main
        id="limit-up-workbench-content"
        className="studio-workspace-surface min-h-0 flex-1 overflow-hidden"
      >
        {workspaceMode === 'REALTIME' ? (
          <section
            id="limit-up-realtime-view"
            aria-label={
              realtimeViews.find(view => view.id === activeView)?.label
            }
            className="h-full min-h-0"
            role="tabpanel"
          >
            {activeView === 'RADAR' ? (
              <LimitUpRadarPanel
                armedCodes={armedCodes}
                assistantEnabled={enabled}
                busyCode={
                  busyAction?.startsWith('candidate:')
                    ? busyAction.slice(10)
                    : null
                }
                candidates={radar.candidates}
                errorMessage={radar.error?.message}
                exitPlanCodes={exitPlanCodes}
                fetching={radar.fetching}
                industries={radar.industries}
                industry={radar.industry}
                isScannerRunning={radar.isScannerRunning}
                onArm={code =>
                  act(`candidate:${code}`, () => assistant.arm(code))
                }
                onDisarm={code =>
                  act(`candidate:${code}`, () => assistant.disarm(code))
                }
                onIndustryChange={radar.setIndustry}
                onSearchChange={radar.setSearch}
                onSelectCandidate={selectCandidate}
                onStageChange={radar.setStage}
                pendingCodes={pendingCodes}
                search={radar.search}
                selectedCode={selectedCode}
                stage={radar.stage}
                summary={radar.summary}
                systemWarnings={systemWarnings}
              />
            ) : activeView === 'SIGNALS' ? (
              <div className="flex h-full min-h-0 flex-col overflow-hidden border border-amber-400/15 bg-[#0d1626]/90">
                <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-white/[0.07] px-3 py-2.5">
                  <div>
                    <h2 className="flex items-center gap-2 text-ui-label font-black">
                      <Clock3 className="h-3.5 w-3.5 text-amber-300" />
                      待确认信号
                    </h2>
                    <p className="mt-1 text-ui-micro text-slate-600">
                      新信号到达时自动切换一次；确认前仍会重新校验行情、资金与风控。
                    </p>
                  </div>
                  <div className="flex items-center gap-2 text-ui-micro text-slate-500">
                    <span>
                      入场门禁{' '}
                      <strong
                        className={
                          assistant.assistant?.canApprove
                            ? 'text-emerald-300'
                            : 'text-amber-300'
                        }
                      >
                        {assistant.assistant?.canApprove ? '已通过' : '未通过'}
                      </strong>
                    </span>
                    <Badge className="border-amber-400/20 bg-amber-400/10 text-ui-micro text-amber-200 hover:bg-amber-400/10">
                      {assistant.pendingIntents.length}
                    </Badge>
                  </div>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar">
                  {assistant.pendingIntents.length ? (
                    <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,22rem),1fr))] gap-3">
                      {assistant.pendingIntents.map(intent => (
                        <PendingSignalCard
                          key={intent.id}
                          intent={intent}
                          busy={busyAction === `intent:${intent.id}`}
                          canApprove={Boolean(assistant.assistant?.canApprove)}
                          onApprove={() =>
                            act(`intent:${intent.id}`, () =>
                              assistant.approve(intent.id)
                            )
                          }
                          onReject={() =>
                            act(`intent:${intent.id}`, () =>
                              assistant.reject(intent.id)
                            )
                          }
                        />
                      ))}
                    </div>
                  ) : (
                    <EmptyWorkspace
                      icon={ShieldCheck}
                      title="暂无待确认信号"
                      description="助手只在候选进入临板触发区时生成一次 15 秒确认卡。你可以回到候选雷达继续观察。"
                      actionLabel="返回候选雷达"
                      onAction={() => setActiveView('RADAR')}
                    />
                  )}
                </div>
              </div>
            ) : (
              <div className="flex h-full min-h-0 flex-col overflow-hidden border border-emerald-400/15 bg-[#0d1626]/90">
                <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-white/[0.07] px-3 py-2.5">
                  <div>
                    <h2 className="flex items-center gap-2 text-ui-label font-black">
                      <WalletCards className="h-3.5 w-3.5 text-emerald-300" />
                      T+1 自适应退出
                    </h2>
                    <p className="mt-1 text-ui-micro text-slate-600">
                      真实成交回报到达后由 Engine 建立唯一退出计划并持续托管。
                    </p>
                  </div>
                  <Button
                    className="h-control-compact border-white/10 bg-white/[0.025] px-2 text-ui-micro text-slate-300 hover:bg-white/[0.06]"
                    onClick={() => setLocation('/liquidation')}
                    size="sm"
                    variant="outline"
                  >
                    打开卖出管理
                  </Button>
                </div>
                <div className="grid shrink-0 grid-flow-col auto-cols-[minmax(150px,1fr)] gap-px overflow-x-auto border-b border-white/[0.06] bg-white/[0.06] custom-scrollbar">
                  <PositionMetric
                    label="托管持仓"
                    value={assistant.exitPlans.length}
                  />
                  <PositionMetric
                    label="退出委托中"
                    value={
                      assistant.exitPlans.filter(plan => plan.pendingOrderId)
                        .length
                    }
                  />
                  <PositionMetric
                    label="计划异常"
                    value={exitPlanErrorCount}
                    alert={exitPlanErrorCount > 0}
                  />
                  <PositionMetric
                    label="未授权"
                    value={
                      assistant.exitPlans.filter(
                        plan => !plan.autoExitAuthorized
                      ).length
                    }
                    alert={assistant.exitPlans.some(
                      plan => !plan.autoExitAuthorized
                    )}
                  />
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar">
                  {assistant.exitPlans.length ? (
                    <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,22rem),1fr))] gap-3">
                      {assistant.exitPlans.map(plan => (
                        <ExitPlanCard key={plan.id} plan={plan} />
                      ))}
                    </div>
                  ) : (
                    <EmptyWorkspace
                      icon={WalletCards}
                      title="暂无托管仓位"
                      description="受托买入的真实成交回报到达后，T+1 退出计划会自动出现在这里。"
                    />
                  )}
                </div>
              </div>
            )}
          </section>
        ) : (
          <section aria-label="回放测试" className="h-full min-h-0">
            <Suspense
              fallback={
                <div
                  aria-label="正在加载历史回放"
                  className="flex h-full items-center justify-center text-ui-caption text-slate-500"
                  role="status"
                >
                  正在加载历史回放…
                </div>
              }
            >
              <LimitUpBoardReplayPanel accountId={accountId} />
            </Suspense>
          </section>
        )}
      </main>
    </div>
  );

  return (
    <>
      <StudioWorkbench
        activeMode={workspaceMode === 'REALTIME' ? activeView : 'REPLAY'}
        className="h-full min-h-0"
        content={content}
        isPage
        modes={workspaceMode === 'REALTIME' ? studioModes : []}
        onModeChange={nextMode =>
          setActiveView(nextMode as LimitUpRealtimeView)
        }
        showSidebar
        sidebar={
          workspaceMode === 'REPLAY' ? (
            replaySidebar
          ) : (
            <LimitUpBoardHealthConsole {...healthConsoleProps} />
          )
        }
        sidebarSizing={{
          defaultWidth: 312,
          maxWidth: 420,
          minWidth: 260,
          storageScope: 'limit-up-board-studio',
        }}
        statusBarLeft={
          <>
            <span className="inline-flex items-center gap-2">
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  workspaceMode === 'REPLAY'
                    ? 'bg-cyan-400'
                    : enabled
                      ? 'bg-emerald-400'
                      : 'bg-slate-600'
                )}
              />
              {workspaceMode === 'REPLAY'
                ? '历史回放测试模式'
                : enabled
                  ? '晋级助手运行中'
                  : '晋级助手已停止'}
            </span>
            <span className="text-slate-700">|</span>
            <span className="font-mono">{accountId || '未配置账户'}</span>
            {workspaceMode === 'REALTIME' ? (
              <>
                <span className="text-slate-700">|</span>
                <span>最近同步 {updatedAt}</span>
              </>
            ) : null}
          </>
        }
        statusBarRight={
          workspaceMode === 'REPLAY' ? (
            <>
              <span>BACKTEST Broker</span>
              <span className="text-slate-700">|</span>
              <span>动态候选 · 四情景</span>
              <span className="text-slate-700">|</span>
              <span>实时监控互不影响</span>
            </>
          ) : (
            <>
              <span>
                {assistant.currentSettings.mode === 'live' ? '实盘' : '模拟盘'}
              </span>
              <span className="text-slate-700">|</span>
              <span>
                候选 {radar.summary.eligibleCount} · 待确认{' '}
                {assistant.pendingIntents.length}
              </span>
              <span className="text-slate-700">|</span>
              <span>T+1 托管 {assistant.exitPlans.length}</span>
            </>
          )
        }
        theme={{
          icon: workspaceMode === 'REPLAY' ? FlaskConical : Target,
          name: workspaceMode === 'REPLAY' ? 'cyan' : 'red',
          title: workspaceMode === 'REPLAY' ? '打板回放测试' : '打板助手',
        }}
      />

      <Sheet
        open={Boolean(selectedCandidate)}
        onOpenChange={open => {
          if (!open) setSelectedCode(null);
        }}
      >
        <SheetContent
          side="right"
          className="w-[92vw] border-white/[0.08] bg-[#081423] p-0 text-slate-100 sm:max-w-[540px]"
          onCloseAutoFocus={event => {
            event.preventDefault();
            candidateTriggerRef.current?.focus();
          }}
        >
          <SheetTitle className="sr-only">
            {selectedCandidate
              ? `${selectedCandidate.name}候选详情`
              : '候选详情'}
          </SheetTitle>
          <SheetDescription className="sr-only">
            查看候选晋级概率、评分拆解、盘口与研究摘要。
          </SheetDescription>
          {selectedCandidate ? (
            <LimitUpCandidateInspector
              candidate={selectedCandidate}
              onOpenStock={code =>
                setLocation(`/stock/${encodeURIComponent(code)}`)
              }
            />
          ) : null}
        </SheetContent>
      </Sheet>

      <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent className="border-white/10 bg-[#0d1626] text-slate-100 sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>首板晋级风险设置</DialogTitle>
            <DialogDescription className="text-slate-500">
              模型阈值冻结且不可调。买入始终人工确认，成交后由 Engine 按 T+1
              计划托管退出。
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-ui-section py-2 sm:grid-cols-2">
            <SettingField label="单标的资产上限（%）">
              <Input
                type="number"
                min={0.5}
                max={30}
                step={0.5}
                value={positionCap}
                onChange={event => setPositionCap(Number(event.target.value))}
                className="border-white/10 bg-[#08111f]"
              />
            </SettingField>
            <SettingField label="当日总暴露上限（%）">
              <Input
                type="number"
                min={0.5}
                max={30}
                step={0.5}
                value={dailyExposureCap}
                onChange={event =>
                  setDailyExposureCap(Number(event.target.value))
                }
                className="border-white/10 bg-[#08111f]"
              />
            </SettingField>
            <SettingField label="单笔计划尾损（净资产 %）">
              <Input
                type="number"
                min={0.01}
                max={2}
                step={0.01}
                value={tailLossBudget}
                onChange={event =>
                  setTailLossBudget(Number(event.target.value))
                }
                className="border-white/10 bg-[#08111f]"
              />
            </SettingField>
            <SettingField label="最多同时持有（只）">
              <Input
                type="number"
                min={1}
                max={10}
                step={1}
                value={maxOpenPositions}
                onChange={event =>
                  setMaxOpenPositions(Number(event.target.value))
                }
                className="border-white/10 bg-[#08111f]"
              />
            </SettingField>
            <SettingField label="运行环境">
              <NativeSelect
                value={mode}
                onChange={event => setMode(event.target.value)}
                className="h-10 w-full rounded-md border border-white/10 bg-[#08111f] px-3 text-ui-body outline-none focus:ring-2 focus:ring-cyan-400/50"
              >
                <option value="paper">模拟盘</option>
                <option value="live">实盘</option>
              </NativeSelect>
            </SettingField>
          </div>
          {mode === 'live' ? (
            <label className="flex items-start gap-2 rounded-lg border border-rose-400/20 bg-rose-500/[0.06] p-3 text-ui-caption leading-5 text-slate-300">
              <Checkbox
                checked={autoExitAcknowledged}
                onCheckedChange={value =>
                  setAutoExitAcknowledged(value === true)
                }
                className="mt-0.5"
              />
              <span>
                我确认：T 日买入不可卖；真实成交后 Engine 会在 T+1
                触及二板、破板、弱势或 14:50 时全量退出。
              </span>
            </label>
          ) : null}
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.025] p-3 text-ui-caption leading-5 text-slate-500">
            默认保守档：单票 2%、当日 6%、单笔尾损 0.15%、最多 2
            只。若一手成本超过风险预算会直接拒绝，不向上取整。
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setSettingsOpen(false)}
              className="border-white/10 bg-transparent text-slate-300 hover:bg-white/[0.05]"
            >
              取消
            </Button>
            <Button
              onClick={saveSettings}
              disabled={
                busyAction === 'settings' ||
                positionCap <= 0 ||
                positionCap > 30 ||
                dailyExposureCap <= 0 ||
                dailyExposureCap > 30 ||
                tailLossBudget <= 0 ||
                tailLossBudget > 2 ||
                maxOpenPositions < 1 ||
                maxOpenPositions > 10 ||
                (mode === 'live' && !autoExitAcknowledged)
              }
              className="bg-red-500 text-white hover:bg-red-400"
            >
              {busyAction === 'settings' ? '保存中' : '保存设置'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function PositionMetric({
  alert = false,
  label,
  value,
}: {
  alert?: boolean;
  label: string;
  value: number;
}) {
  return (
    <div className="bg-[#0b1423] px-3 py-2">
      <span className="text-ui-micro text-slate-600">{label}</span>
      <strong
        className={cn(
          'ml-2 font-mono text-ui-body',
          alert ? 'text-rose-300' : 'text-slate-200'
        )}
      >
        {value}
      </strong>
    </div>
  );
}

function EmptyWorkspace({
  actionLabel,
  description,
  icon: Icon,
  onAction,
  title,
}: {
  actionLabel?: string;
  description: string;
  icon: typeof ShieldCheck;
  onAction?: () => void;
  title: string;
}) {
  return (
    <div className="flex h-full min-h-56 items-center justify-center">
      <div className="max-w-sm text-center">
        <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full border border-white/[0.07] bg-white/[0.025]">
          <Icon className="h-5 w-5 text-slate-700" />
        </div>
        <div className="mt-3 text-ui-label font-black text-slate-300">
          {title}
        </div>
        <p className="mt-1.5 text-ui-caption leading-5 text-slate-600">
          {description}
        </p>
        {actionLabel && onAction ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-3 h-control-compact border-white/10 bg-transparent text-ui-caption text-slate-300 hover:bg-white/[0.05]"
            onClick={onAction}
          >
            <LayoutList className="mr-1.5 h-3.5 w-3.5" />
            {actionLabel}
          </Button>
        ) : null}
      </div>
    </div>
  );
}

function SettingField({
  children,
  label,
}: {
  children: ReactNode;
  label: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-ui-caption font-bold text-slate-400">
        {label}
      </Label>
      {children}
    </div>
  );
}
