import {
  Activity,
  AlertTriangle,
  BarChart3,
  Ban,
  CalendarDays,
  Check,
  CircleDollarSign,
  Clock3,
  FlaskConical,
  History,
  Loader2,
  Plus,
  Play,
  Radar,
  RefreshCw,
  Save,
  Settings2,
  ShieldCheck,
  Square,
  TrendingUp,
  WalletCards,
  X,
} from 'lucide-react';
import * as React from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useMutation, useQuery } from 'urql';

import {
  StudioWorkbench,
  type StudioMode,
} from '@/components/studio-workbench';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { TTradeTimeExitMode } from '@/generated/gql/graphql';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/utils/cn';

import { useCurrentAccount } from '../../dashboard/hooks';
import {
  ApproveTTradeEntryMutation,
  GetHoldingsQuery,
  GetPortfolioSummaryQuery,
  RejectTTradeEntryMutation,
} from '../hooks/usePortfolio';
import {
  CancelTTradeReplayMutation,
  ImportTTradeExternalEntryMutation,
  ReconcileTTradeGlobalMonitorMutation,
  SaveTTradeGlobalMonitorMutation,
  StartTTradeReplayMutation,
  SyncTTradeSourceOrdersMutation,
  TTradeGlobalMonitorQuery,
  TTradeInstrumentNameQuery,
  TTradeReplayCyclesQuery,
  TTradeReplayHistoryQuery,
  TTradeReplayPreparationQuery,
  TTradeReplayQuery,
  TTradeSourceOrdersQuery,
} from '../hooks/useTTradeGlobal';

type SettingsForm = {
  mode: 'paper' | 'live';
  acknowledged: boolean;
  targetTradeAmount: string;
  maxTradeAmount: string;
  maxConcurrentBatches: string;
  maxTotalTExposurePct: string;
  targetProfitPct: string;
  baseFloorPct: string;
  initialGapPct: string;
  trailingGapSlope: string;
  maxGapPct: string;
  hardStopEnabled: boolean;
  hardStopPct: string;
  signalLookbackSeconds: string;
  stabilizationSeconds: string;
  pullbackThresholdPct: string;
  reboundThresholdPct: string;
  maxSpreadTicks: string;
  approvalTtlSeconds: string;
  maxPriceDeviationPct: string;
  timeExitMode: TTradeTimeExitMode;
  timeExitTime: string;
  maxHoldingTradingDays: string;
  cooldownSeconds: string;
};

type TTradeStudioMode = 'MONITOR' | 'SIGNALS' | 'SETTINGS';

const tTradeModes: StudioMode[] = [
  { id: 'MONITOR', icon: Radar, label: '监控' },
  { id: 'SIGNALS', icon: Activity, label: '待确认' },
  { id: 'SETTINGS', icon: Settings2, label: '参数' },
];

const defaultForm: SettingsForm = {
  mode: 'paper',
  acknowledged: false,
  targetTradeAmount: '10000',
  maxTradeAmount: '12000',
  maxConcurrentBatches: '3',
  maxTotalTExposurePct: '10',
  targetProfitPct: '2',
  baseFloorPct: '0.5',
  initialGapPct: '1.5',
  trailingGapSlope: '0.25',
  maxGapPct: '3',
  hardStopEnabled: false,
  hardStopPct: '-0.8',
  signalLookbackSeconds: '300',
  stabilizationSeconds: '15',
  pullbackThresholdPct: '0.8',
  reboundThresholdPct: '0.2',
  maxSpreadTicks: '3',
  approvalTtlSeconds: '30',
  maxPriceDeviationPct: '0.3',
  timeExitMode: TTradeTimeExitMode.Unlimited,
  timeExitTime: '14:50',
  maxHoldingTradingDays: '5',
  cooldownSeconds: '300',
};

const statusPresentation: Record<string, { label: string; className: string }> =
  {
    DRAINING: {
      label: '退出中',
      className: 'border-amber-400/25 bg-amber-400/10 text-amber-200',
    },
    IGNORED: {
      label: '已忽略',
      className: 'border-slate-400/20 bg-slate-400/10 text-slate-400',
    },
    INELIGIBLE: {
      label: '暂不可用',
      className: 'border-slate-400/20 bg-slate-400/10 text-slate-400',
    },
    MONITORED: {
      label: '监控中',
      className: 'border-red-400/25 bg-red-400/10 text-red-200',
    },
    PENDING_START: {
      label: '待启动',
      className: 'border-blue-400/25 bg-blue-400/10 text-blue-200',
    },
    STOPPED: {
      label: '未启动',
      className: 'border-white/10 bg-white/[0.04] text-slate-500',
    },
  };

function numberValue(value: string, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function integerValue(value: string, fallback: number) {
  return Math.trunc(numberValue(value, fallback));
}

function formatNumber(value: number, digits = 2) {
  return Number(value || 0).toLocaleString('zh-CN', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function formatTime(value?: string | null) {
  if (!value) return '尚未同步';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', { hour12: false });
}

function hasInstrumentName(stockCode: string, candidate?: string | null) {
  const value = String(candidate || '').trim();
  const normalizedCode = stockCode.trim().toUpperCase();
  const codeWithoutExchange = normalizedCode.split('.', 1)[0];
  return Boolean(
    value &&
      ![normalizedCode, codeWithoutExchange].includes(value.toUpperCase())
  );
}

function resolveInstrumentName(
  stockCode: string,
  positionName?: string | null,
  monitorName?: string | null
) {
  if (hasInstrumentName(stockCode, positionName)) return positionName!.trim();
  if (hasInstrumentName(stockCode, monitorName)) return monitorName!.trim();
  return stockCode;
}

function InstrumentNameLabel({
  className,
  knownName,
  stockCode,
}: {
  className?: string;
  knownName?: string | null;
  stockCode: string;
}) {
  const needsLookup = !hasInstrumentName(stockCode, knownName);
  const [result] = useQuery({
    query: TTradeInstrumentNameQuery,
    variables: { stockCode },
    pause: !needsLookup,
    requestPolicy: 'cache-first',
  });
  const instrumentName = resolveInstrumentName(
    stockCode,
    result.data?.instrument?.name,
    knownName
  );
  return <div className={className}>{instrumentName}</div>;
}

function MetricCard({
  icon: Icon,
  label,
  tone = 'slate',
  value,
}: {
  icon: React.ElementType;
  label: string;
  tone?: 'amber' | 'emerald' | 'red' | 'slate';
  value: string | number;
}) {
  const tones = {
    amber: 'border-amber-400/15 bg-amber-400/[0.06] text-amber-200',
    emerald: 'border-emerald-400/15 bg-emerald-400/[0.06] text-emerald-200',
    red: 'border-red-400/15 bg-red-400/[0.06] text-red-200',
    slate: 'border-white/[0.07] bg-white/[0.025] text-slate-200',
  };
  return (
    <div className={cn('border p-2.5', tones[tone])}>
      <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.12em] opacity-70">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <div className="mt-1.5 font-mono text-lg font-black tabular-nums">
        {value}
      </div>
    </div>
  );
}

function NumericField({
  disabled = false,
  id,
  label,
  onChange,
  suffix,
  value,
}: {
  disabled?: boolean;
  id: string;
  label: string;
  onChange: (value: string) => void;
  suffix?: string;
  value: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id} className="text-xs font-bold text-slate-400">
        {label}
      </Label>
      <div className="relative">
        <Input
          id={id}
          disabled={disabled}
          inputMode="decimal"
          value={value}
          onChange={event => onChange(event.target.value)}
          className="h-9 rounded-sm border-white/10 bg-[#07111f] pr-10 font-mono text-xs focus-visible:ring-red-500/60 disabled:cursor-not-allowed disabled:opacity-50"
        />
        {suffix && (
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[10px] font-bold text-slate-600">
            {suffix}
          </span>
        )}
      </div>
    </div>
  );
}

function replayDatePreset(tradingDays: 1 | 5 | 20) {
  const end = new Date();
  const start = new Date(end);
  let counted = 1;
  while (counted < tradingDays) {
    start.setDate(start.getDate() - 1);
    const day = start.getDay();
    if (day !== 0 && day !== 6) counted += 1;
  }
  const format = (value: Date) => {
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, '0');
    const day = String(value.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  };
  return { start: format(start), end: format(end) };
}

function replayStatusLabel(status?: string | null) {
  const labels: Record<string, string> = {
    CANCELLED: '已取消',
    COMPLETED: '已完成',
    ERROR: '失败',
    PENDING: '等待中',
    RUNNING: '回放中',
    STARTING: '启动中',
    STOPPED: '已停止',
  };
  return labels[String(status || '').toUpperCase()] || status || '未知';
}

function TTradeReplayPanel({
  accountId,
  form,
}: {
  accountId: string;
  form: SettingsForm;
}) {
  const { toast } = useToast();
  const initialRange = React.useMemo(() => replayDatePreset(5), []);
  const [startDate, setStartDate] = React.useState(initialRange.start);
  const [endDate, setEndDate] = React.useState(initialRange.end);
  const [activeRunId, setActiveRunId] = React.useState('');
  const [useCurrentPortfolio, setUseCurrentPortfolio] = React.useState(false);
  const startTime = `${startDate}T09:30:00`;
  const endTime = `${endDate}T15:00:00`;

  const [preparationResult, _refreshPreparation] = useQuery({
    query: TTradeReplayPreparationQuery,
    variables: { accountId, startTime },
    pause: !accountId || !startDate,
    requestPolicy: 'network-only',
  });
  const [manualHoldingsResult] = useQuery({
    query: GetHoldingsQuery,
    variables: { accountId },
    pause: !accountId,
    requestPolicy: 'cache-and-network',
  });
  const [portfolioSummaryResult] = useQuery({
    query: GetPortfolioSummaryQuery,
    variables: { accountId },
    pause: !accountId,
    requestPolicy: 'cache-and-network',
  });
  const [historyResult, refreshHistory] = useQuery({
    query: TTradeReplayHistoryQuery,
    variables: { accountId, limit: 20 },
    pause: !accountId,
    requestPolicy: 'network-only',
  });
  const [replayResult, refreshReplay] = useQuery({
    query: TTradeReplayQuery,
    variables: { runId: activeRunId },
    pause: !activeRunId,
    requestPolicy: 'network-only',
  });
  const [cyclesResult, refreshCycles] = useQuery({
    query: TTradeReplayCyclesQuery,
    variables: { runId: activeRunId, offset: 0, limit: 100 },
    pause: !activeRunId,
    requestPolicy: 'network-only',
  });
  const [startResult, startReplay] = useMutation(StartTTradeReplayMutation);
  const [cancelResult, cancelReplay] = useMutation(CancelTTradeReplayMutation);

  const history = React.useMemo(
    () => historyResult.data?.tTradeReplayHistory || [],
    [historyResult.data?.tTradeReplayHistory]
  );
  const replay = replayResult.data?.tTradeReplay;
  const preparation = preparationResult.data?.tTradeReplayPreparation;
  const cycles = cyclesResult.data?.tTradeReplayCycles.items || [];
  const currentPortfolioSummary = portfolioSummaryResult.data?.portfolioSummary;
  const currentPortfolioPositions = React.useMemo(
    () =>
      (manualHoldingsResult.data?.positions || [])
        .filter(
          position => position.accountId === accountId && position.volume > 0
        )
        .map(position => ({
          stockCode: position.stockCode,
          instrumentName: position.instrumentName || '',
          volume: Math.max(0, Math.trunc(position.volume)),
          availableVolume: Math.max(0, Math.trunc(position.canUseVolume)),
          avgPrice: Math.max(0, position.avgPrice || 0),
          lastPrice: Math.max(0, position.lastPrice || 0),
          marketValue: Math.max(0, position.marketValue || 0),
        })),
    [accountId, manualHoldingsResult.data?.positions]
  );
  const canUseCurrentPortfolio = Boolean(
    currentPortfolioSummary && currentPortfolioPositions.length > 0
  );
  const isRunning = ['PENDING', 'RUNNING', 'STARTING'].includes(
    String(replay?.status || '').toUpperCase()
  );

  React.useEffect(() => {
    setUseCurrentPortfolio(false);
  }, [accountId, startDate]);

  React.useEffect(() => {
    if (!activeRunId && history.length > 0) setActiveRunId(history[0].runId);
  }, [activeRunId, history]);

  React.useEffect(() => {
    if (!accountId) return;
    const timer = window.setInterval(() => {
      refreshHistory({ requestPolicy: 'network-only' });
      if (activeRunId) {
        refreshReplay({ requestPolicy: 'network-only' });
        refreshCycles({ requestPolicy: 'network-only' });
      }
    }, 2_500);
    return () => window.clearInterval(timer);
  }, [accountId, activeRunId, refreshCycles, refreshHistory, refreshReplay]);

  const setPreset = (days: 1 | 5 | 20) => {
    const range = replayDatePreset(days);
    setStartDate(range.start);
    setEndDate(range.end);
  };

  const handleStart = async () => {
    try {
      const useManualPortfolio = Boolean(
        preparation?.requiresManualPortfolio && useCurrentPortfolio
      );
      const result = await startReplay({
        input: {
          accountId,
          startTime,
          endTime,
          initialCash: useManualPortfolio
            ? Math.max(0, currentPortfolioSummary?.cash || 0)
            : undefined,
          initialTotalAsset: useManualPortfolio
            ? Math.max(0, currentPortfolioSummary?.totalAsset || 0)
            : undefined,
          initialPositions: useManualPortfolio ? currentPortfolioPositions : [],
          targetTradeAmount: numberValue(form.targetTradeAmount, 10000),
          maxTradeAmount: numberValue(form.maxTradeAmount, 12000),
          maxConcurrentBatches: integerValue(form.maxConcurrentBatches, 3),
          maxTotalTExposurePct:
            numberValue(form.maxTotalTExposurePct, 10) / 100,
          signalLookbackSeconds: integerValue(form.signalLookbackSeconds, 300),
          stabilizationSeconds: integerValue(form.stabilizationSeconds, 15),
          pullbackThresholdPct: numberValue(form.pullbackThresholdPct, 0.8),
          reboundThresholdPct: numberValue(form.reboundThresholdPct, 0.2),
          maxSpreadTicks: integerValue(form.maxSpreadTicks, 3),
          approvalTtlSeconds: integerValue(form.approvalTtlSeconds, 30),
          maxPriceDeviationPct: numberValue(form.maxPriceDeviationPct, 0.3),
          targetProfitPct: numberValue(form.targetProfitPct, 2),
          baseFloorPct: numberValue(form.baseFloorPct, 0.5),
          initialGapPct: numberValue(form.initialGapPct, 1.5),
          trailingGapSlope: numberValue(form.trailingGapSlope, 0.25),
          maxGapPct: numberValue(form.maxGapPct, 3),
          hardStopEnabled: form.hardStopEnabled,
          hardStopPct: numberValue(form.hardStopPct, -0.8),
          timeExitMode: form.timeExitMode,
          timeExitTime: form.timeExitTime,
          maxHoldingTradingDays: integerValue(form.maxHoldingTradingDays, 5),
          cooldownSeconds: integerValue(form.cooldownSeconds, 300),
        },
      });
      const payload = result.data?.startTTradeReplay;
      if (!payload?.success || !payload.replay?.runId) {
        throw new Error(
          payload?.message || result.error?.message || '启动失败'
        );
      }
      setActiveRunId(payload.replay.runId);
      toast({ title: '历史回放已启动', description: payload.message });
      refreshHistory({ requestPolicy: 'network-only' });
    } catch (error) {
      toast({
        title: '无法启动历史回放',
        description: error instanceof Error ? error.message : '请求失败',
        variant: 'destructive',
      });
    }
  };

  const handleCancel = async () => {
    if (!activeRunId) return;
    try {
      const result = await cancelReplay({ runId: activeRunId });
      const payload = result.data?.cancelTTradeReplay;
      if (!payload?.success) {
        throw new Error(
          payload?.message || result.error?.message || '取消失败'
        );
      }
      toast({ title: '回放已取消', description: payload.message });
      refreshReplay({ requestPolicy: 'network-only' });
      refreshHistory({ requestPolicy: 'network-only' });
    } catch (error) {
      toast({
        title: '无法取消回放',
        description: error instanceof Error ? error.message : '请求失败',
        variant: 'destructive',
      });
    }
  };

  const chartData = (replay?.curve || []).map(point => ({
    time: new Date(point.timestamp).toLocaleString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      month: '2-digit',
      day: '2-digit',
      hour12: false,
    }),
    账户收益: Number(point.returnPct.toFixed(4)),
    不做T基准: Number(point.passiveReturnPct.toFixed(4)),
    做T增量: Number(point.excessReturnPct.toFixed(4)),
  }));

  return (
    <div className="grid h-full min-h-0 grid-cols-1 bg-[#081321] xl:grid-cols-[minmax(0,1fr)_300px]">
      <div className="min-h-0 overflow-y-auto custom-scrollbar">
        <section className="border-b border-white/[0.06] bg-[#0a1728] p-4">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-sm font-black text-slate-100">
                <FlaskConical className="h-4 w-4 text-cyan-300" />
                历史回放测试
              </div>
              <p className="mt-1 text-[11px] text-slate-500">
                使用同一做 T
                策略和交易风控；测试信号自动确认，不会提交实盘委托。
              </p>
            </div>
            <div className="flex flex-wrap items-end gap-2">
              <div>
                <Label
                  htmlFor="replay-start"
                  className="text-[10px] text-slate-500"
                >
                  开始日期
                </Label>
                <Input
                  id="replay-start"
                  type="date"
                  value={startDate}
                  max={endDate}
                  onChange={event => setStartDate(event.target.value)}
                  className="mt-1 h-8 w-36 rounded-sm border-white/10 bg-[#07111f] text-xs"
                />
              </div>
              <div>
                <Label
                  htmlFor="replay-end"
                  className="text-[10px] text-slate-500"
                >
                  结束日期
                </Label>
                <Input
                  id="replay-end"
                  type="date"
                  value={endDate}
                  min={startDate}
                  onChange={event => setEndDate(event.target.value)}
                  className="mt-1 h-8 w-36 rounded-sm border-white/10 bg-[#07111f] text-xs"
                />
              </div>
              <div className="flex h-8 overflow-hidden border border-white/10">
                {([1, 5, 20] as const).map(days => (
                  <button
                    key={days}
                    type="button"
                    onClick={() => setPreset(days)}
                    className="cursor-pointer border-r border-white/10 px-2.5 text-[10px] font-bold text-slate-400 transition-colors last:border-r-0 hover:bg-white/[0.06] hover:text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-400/60"
                  >
                    {days}日
                  </button>
                ))}
              </div>
              <Button
                type="button"
                size="sm"
                onClick={handleStart}
                disabled={
                  !accountId ||
                  !preparation ||
                  (preparation.requiresManualPortfolio &&
                    (!useCurrentPortfolio || !canUseCurrentPortfolio)) ||
                  startResult.fetching ||
                  history.some(item =>
                    ['PENDING', 'RUNNING', 'STARTING'].includes(item.status)
                  )
                }
                className="h-8 rounded-sm bg-cyan-500 px-3 text-[10px] font-black text-slate-950 hover:bg-cyan-400"
              >
                {startResult.fetching ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Play className="mr-1.5 h-3.5 w-3.5" />
                )}
                启动回放
              </Button>
            </div>
          </div>

          <div
            className={cn(
              'mt-3 flex items-start gap-2 border px-3 py-2 text-[11px]',
              preparation?.requiresManualPortfolio || preparationResult.error
                ? 'border-amber-400/20 bg-amber-400/[0.06] text-amber-100'
                : 'border-cyan-400/15 bg-cyan-400/[0.04] text-cyan-100'
            )}
          >
            {preparationResult.fetching ? (
              <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin" />
            ) : preparation?.requiresManualPortfolio ||
              preparationResult.error ? (
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            ) : (
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            )}
            <span>
              {preparationResult.error?.message ||
                preparation?.message ||
                '正在读取回放开始日前的账户快照…'}
              {preparation?.snapshotDate && (
                <span className="ml-2 font-mono text-slate-400">
                  快照 {preparation.snapshotDate} ·{' '}
                  {preparation.positions.length} 只持仓 · 总资产 ¥
                  {formatNumber(preparation.initialTotalAsset)}
                </span>
              )}
            </span>
          </div>

          {preparation?.requiresManualPortfolio && (
            <div className="mt-2 flex flex-wrap items-center justify-between gap-3 border border-amber-400/15 bg-amber-400/[0.035] px-3 py-2">
              <div>
                <p className="text-[11px] font-bold text-amber-100">
                  可显式改用当前账户状态作为初始组合
                </p>
                <p className="mt-0.5 text-[10px] text-amber-200/55">
                  当前组合不是历史时点数据，可能产生持仓偏差；仅在你确认后才会用于回放。
                </p>
              </div>
              <button
                type="button"
                aria-pressed={useCurrentPortfolio}
                disabled={
                  manualHoldingsResult.fetching ||
                  portfolioSummaryResult.fetching ||
                  !canUseCurrentPortfolio
                }
                onClick={() => setUseCurrentPortfolio(value => !value)}
                className={cn(
                  'cursor-pointer border px-3 py-1.5 text-[10px] font-black transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60 disabled:cursor-not-allowed disabled:opacity-45',
                  useCurrentPortfolio
                    ? 'border-cyan-400/40 bg-cyan-400/15 text-cyan-100'
                    : 'border-amber-300/25 bg-amber-300/[0.06] text-amber-100 hover:bg-amber-300/10'
                )}
              >
                {manualHoldingsResult.fetching ||
                portfolioSummaryResult.fetching
                  ? '正在读取当前组合…'
                  : useCurrentPortfolio
                    ? `已确认当前组合 · ${currentPortfolioPositions.length} 只`
                    : canUseCurrentPortfolio
                      ? '确认使用当前组合'
                      : '当前组合不可用'}
              </button>
            </div>
          )}
        </section>

        {replay ? (
          <>
            <section className="border-b border-white/[0.06] p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <span
                    className={cn(
                      'border px-2 py-1 text-[10px] font-black',
                      replay.status === 'COMPLETED'
                        ? 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200'
                        : replay.status === 'ERROR'
                          ? 'border-rose-400/25 bg-rose-400/10 text-rose-200'
                          : 'border-cyan-400/25 bg-cyan-400/10 text-cyan-200'
                    )}
                  >
                    {replayStatusLabel(replay.status)}
                  </span>
                  <span className="font-mono text-[10px] text-slate-500">
                    {replay.runId.slice(0, 8)} ·{' '}
                    {formatNumber(replay.progressPct, 1)}%
                  </span>
                  <span className="text-[10px] text-slate-600">
                    {replay.dataQualityMessage}
                  </span>
                </div>
                {isRunning && (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={cancelResult.fetching}
                    onClick={handleCancel}
                    className="h-8 rounded-sm border-rose-400/20 bg-rose-400/[0.04] text-[10px] text-rose-200 hover:bg-rose-400/10"
                  >
                    {cancelResult.fetching ? (
                      <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Square className="mr-1.5 h-3 w-3" />
                    )}
                    取消回放
                  </Button>
                )}
              </div>
              {replay.errorMessage && (
                <div className="mt-3 flex items-center gap-2 border border-rose-400/20 bg-rose-400/[0.06] px-3 py-2 text-[11px] text-rose-100">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  {replay.errorMessage}
                </div>
              )}
            </section>

            <section className="grid gap-2 border-b border-white/[0.06] p-4 sm:grid-cols-2 xl:grid-cols-4">
              <MetricCard
                icon={CircleDollarSign}
                label="做 T 税费后增量"
                tone={
                  (replay.summary?.tNetProfit || 0) >= 0 ? 'red' : 'emerald'
                }
                value={
                  replay.summary
                    ? `¥${formatNumber(replay.summary.tNetProfit)}`
                    : '--'
                }
              />
              <MetricCard
                icon={TrendingUp}
                label="相对不做 T 超额"
                tone={
                  (replay.summary?.excessReturnPct || 0) >= 0
                    ? 'red'
                    : 'emerald'
                }
                value={
                  replay.summary
                    ? `${formatNumber(replay.summary.excessReturnPct)}%`
                    : '--'
                }
              />
              <MetricCard
                icon={Check}
                label="完成批次 / 胜率"
                tone="slate"
                value={
                  replay.summary
                    ? `${replay.summary.completedCycles} / ${formatNumber(replay.summary.winRatePct, 1)}%`
                    : '--'
                }
              />
              <MetricCard
                icon={WalletCards}
                label="交易税费"
                tone="amber"
                value={
                  replay.summary
                    ? `¥${formatNumber(replay.summary.totalFees)}`
                    : '--'
                }
              />
            </section>

            <section className="border-b border-white/[0.06] p-4">
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h3 className="flex items-center gap-2 text-xs font-black text-slate-200">
                    <BarChart3 className="h-4 w-4 text-cyan-300" />
                    账户收益与不做 T 基准
                  </h3>
                  <p className="mt-1 text-[10px] text-slate-600">
                    同一初始现金和持仓按历史价格估值，差值为做 T 税费后增量。
                  </p>
                </div>
              </div>
              <div className="h-64 border border-white/[0.06] bg-[#07111f] p-2">
                {chartData.length > 1 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={chartData}>
                      <CartesianGrid
                        stroke="rgba(148,163,184,0.08)"
                        vertical={false}
                      />
                      <XAxis
                        dataKey="time"
                        minTickGap={40}
                        tick={{ fill: '#64748b', fontSize: 9 }}
                        axisLine={{ stroke: 'rgba(148,163,184,0.12)' }}
                        tickLine={false}
                      />
                      <YAxis
                        width={48}
                        tickFormatter={value => `${value}%`}
                        tick={{ fill: '#64748b', fontSize: 9 }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <Tooltip
                        contentStyle={{
                          background: '#0b1628',
                          border: '1px solid rgba(148,163,184,0.18)',
                          borderRadius: 2,
                          fontSize: 11,
                        }}
                        formatter={value =>
                          `${formatNumber(Number(value), 3)}%`
                        }
                      />
                      <Legend wrapperStyle={{ fontSize: 10 }} />
                      <Line
                        type="monotone"
                        dataKey="账户收益"
                        stroke="#22d3ee"
                        dot={false}
                        strokeWidth={1.5}
                      />
                      <Line
                        type="monotone"
                        dataKey="不做T基准"
                        stroke="#94a3b8"
                        dot={false}
                        strokeWidth={1.2}
                      />
                      <Line
                        type="monotone"
                        dataKey="做T增量"
                        stroke="#fb7185"
                        dot={false}
                        strokeWidth={1.4}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="flex h-full flex-col items-center justify-center text-center text-[11px] text-slate-600">
                    <History className="mb-2 h-6 w-6 text-slate-700" />
                    回放产生数据后显示收益曲线
                  </div>
                )}
              </div>
            </section>

            <section className="p-4">
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-xs font-black text-slate-200">
                  T 批次明细
                </h3>
                <span className="font-mono text-[10px] text-slate-600">
                  {cyclesResult.data?.tTradeReplayCycles.total || 0} 批
                </span>
              </div>
              <div className="overflow-x-auto border border-white/[0.06]">
                <table className="w-full min-w-[880px] text-left text-[10px]">
                  <thead className="bg-white/[0.025] text-slate-500">
                    <tr>
                      <th className="px-3 py-2">标的 / 批次</th>
                      <th className="px-3 py-2">状态</th>
                      <th className="px-3 py-2 text-right">买入</th>
                      <th className="px-3 py-2 text-right">卖出</th>
                      <th className="px-3 py-2 text-right">税费</th>
                      <th className="px-3 py-2 text-right">净增量</th>
                      <th className="px-3 py-2">退出原因</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cycles.map(cycle => (
                      <tr
                        key={cycle.batchId}
                        className="border-t border-white/[0.05] text-slate-400"
                      >
                        <td className="px-3 py-2">
                          <div className="font-mono font-bold text-slate-200">
                            {cycle.stockCode}
                          </div>
                          <div className="mt-0.5 font-mono text-[9px] text-slate-700">
                            {cycle.batchId.slice(0, 12)}
                          </div>
                        </td>
                        <td className="px-3 py-2">
                          {cycle.status === 'COMPLETED'
                            ? '已完成'
                            : `未平 ${cycle.openVolume} 股`}
                        </td>
                        <td className="px-3 py-2 text-right font-mono">
                          {cycle.entryVolume} @{' '}
                          {formatNumber(cycle.entryAvgPrice, 3)}
                        </td>
                        <td className="px-3 py-2 text-right font-mono">
                          {cycle.exitVolume} @{' '}
                          {formatNumber(cycle.exitAvgPrice, 3)}
                        </td>
                        <td className="px-3 py-2 text-right font-mono">
                          ¥{formatNumber(cycle.totalFees)}
                        </td>
                        <td
                          className={cn(
                            'px-3 py-2 text-right font-mono font-bold',
                            cycle.netProfit >= 0
                              ? 'text-red-300'
                              : 'text-emerald-300'
                          )}
                        >
                          ¥{formatNumber(cycle.netProfit)}
                        </td>
                        <td className="px-3 py-2">
                          {cycle.exitReason || '--'}
                        </td>
                      </tr>
                    ))}
                    {!cyclesResult.fetching && cycles.length === 0 && (
                      <tr>
                        <td
                          colSpan={7}
                          className="px-3 py-8 text-center text-slate-600"
                        >
                          暂无成交批次
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        ) : (
          <div className="flex min-h-[360px] flex-col items-center justify-center px-6 text-center">
            <FlaskConical className="h-10 w-10 text-slate-700" />
            <h2 className="mt-4 text-sm font-black text-slate-300">
              选择日期并启动第一次回放
            </h2>
            <p className="mt-2 max-w-md text-[11px] leading-5 text-slate-600">
              系统将读取开始日前最近的账户日结快照，并用历史 Tick
              数据按时间顺序重放全部合格持仓。
            </p>
          </div>
        )}
      </div>

      <aside className="min-h-0 overflow-y-auto border-l border-white/[0.06] bg-[#091523] custom-scrollbar">
        <div className="flex h-11 items-center justify-between border-b border-white/[0.06] px-3">
          <span className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-500">
            回放记录
          </span>
          <button
            type="button"
            aria-label="刷新历史回放记录"
            onClick={() => refreshHistory({ requestPolicy: 'network-only' })}
            className="cursor-pointer text-slate-600 transition-colors hover:text-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60"
          >
            <RefreshCw
              className={cn(
                'h-3.5 w-3.5',
                historyResult.fetching && 'animate-spin'
              )}
            />
          </button>
        </div>
        {history.map(item => (
          <button
            key={item.runId}
            type="button"
            onClick={() => setActiveRunId(item.runId)}
            className={cn(
              'block w-full cursor-pointer border-b border-white/[0.05] px-3 py-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-400/60',
              activeRunId === item.runId
                ? 'bg-cyan-400/[0.07]'
                : 'hover:bg-white/[0.025]'
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5 text-[10px] font-bold text-slate-300">
                <CalendarDays className="h-3.5 w-3.5 text-slate-600" />
                {String(item.startTime).slice(0, 10)}
              </span>
              <span className="text-[9px] font-black text-cyan-300">
                {replayStatusLabel(item.status)}
              </span>
            </div>
            <div className="mt-2 flex items-center justify-between font-mono text-[10px] text-slate-600">
              <span>{item.runId.slice(0, 8)}</span>
              <span
                className={
                  (item.summary?.tNetProfit || 0) >= 0
                    ? 'text-red-300'
                    : 'text-emerald-300'
                }
              >
                {item.summary
                  ? `¥${formatNumber(item.summary.tNetProfit)}`
                  : `${formatNumber(item.progressPct, 0)}%`}
              </span>
            </div>
          </button>
        ))}
        {!historyResult.fetching && history.length === 0 && (
          <div className="px-4 py-12 text-center text-[11px] text-slate-600">
            暂无历史回放
          </div>
        )}
      </aside>
    </div>
  );
}

export function TTradeGlobalPage() {
  const { toast } = useToast();
  const { data: accountData } = useCurrentAccount();
  const [workspaceMode, setWorkspaceMode] = React.useState<
    'REALTIME' | 'REPLAY'
  >('REALTIME');
  const [activeMode, setActiveMode] =
    React.useState<TTradeStudioMode>('MONITOR');
  const [accountId, setAccountId] = React.useState('');
  const [form, setForm] = React.useState<SettingsForm>(defaultForm);
  const [ignoredCodes, setIgnoredCodes] = React.useState<string[]>([]);
  const [ignoreInput, setIgnoreInput] = React.useState('');
  const [lastMonitorRefreshAt, setLastMonitorRefreshAt] =
    React.useState<Date | null>(null);
  const [manualRefreshPending, setManualRefreshPending] = React.useState(false);
  const hydratedVersionRef = React.useRef('');
  const lastMonitorRefreshRequestRef = React.useRef(0);
  const autoSyncedSourceOrdersAccountRef = React.useRef('');
  const lastSourceOrdersSyncRequestRef = React.useRef(0);

  const [holdingsResult] = useQuery({
    query: GetHoldingsQuery,
    variables: { accountId: accountData?.currentAccount?.id },
    pause: !accountData?.currentAccount?.id,
    requestPolicy: 'cache-and-network',
  });
  const accounts = React.useMemo(() => {
    const unique = new Set(
      (holdingsResult.data?.positions || [])
        .map(position => position.accountId)
        .filter((value): value is string => Boolean(value))
    );
    const currentAccountId = accountData?.currentAccount?.id;
    if (currentAccountId) unique.add(currentAccountId);
    return Array.from(unique).sort();
  }, [accountData?.currentAccount?.id, holdingsResult.data?.positions]);
  const positionNamesByCode = React.useMemo(() => {
    const names = new Map<string, string>();
    for (const position of holdingsResult.data?.positions || []) {
      if (position.accountId !== accountId) continue;
      const instrumentName = resolveInstrumentName(
        position.stockCode,
        position.instrumentName
      );
      if (!hasInstrumentName(position.stockCode, instrumentName)) {
        continue;
      }
      names.set(position.stockCode.toUpperCase(), instrumentName);
    }
    return names;
  }, [accountId, holdingsResult.data?.positions]);

  React.useEffect(() => {
    if (!accountId && accounts.length > 0) setAccountId(accounts[0]);
    if (accountId && accounts.length > 0 && !accounts.includes(accountId)) {
      setAccountId(accounts[0]);
    }
  }, [accountId, accounts]);

  const [monitorResult, refreshMonitor] = useQuery({
    query: TTradeGlobalMonitorQuery,
    variables: { accountId },
    pause: !accountId,
    requestPolicy: 'network-only',
  });
  const [saveResult, saveMonitor] = useMutation(
    SaveTTradeGlobalMonitorMutation
  );
  const [reconcileResult, reconcileMonitor] = useMutation(
    ReconcileTTradeGlobalMonitorMutation
  );
  const [approveResult, approveEntry] = useMutation(ApproveTTradeEntryMutation);
  const [rejectResult, rejectEntry] = useMutation(RejectTTradeEntryMutation);
  const [importResult, importExternalEntry] = useMutation(
    ImportTTradeExternalEntryMutation
  );
  const [syncSourceOrdersResult, syncSourceOrders] = useMutation(
    SyncTTradeSourceOrdersMutation
  );
  const [showExternalEntry, setShowExternalEntry] = React.useState(false);
  const [selectedOrderId, setSelectedOrderId] = React.useState('');
  const [sourceStartDate, setSourceStartDate] = React.useState(() => {
    const date = new Date();
    date.setDate(date.getDate() - 30);
    return date.toISOString().slice(0, 10);
  });
  const [sourceEndDate, setSourceEndDate] = React.useState(() =>
    new Date().toISOString().slice(0, 10)
  );
  const [externalAcknowledged, setExternalAcknowledged] = React.useState(false);
  const [sourceOrdersSyncError, setSourceOrdersSyncError] = React.useState('');
  const [sourceOrdersSyncedAt, setSourceOrdersSyncedAt] =
    React.useState<Date | null>(null);
  const monitor = monitorResult.data?.tTradeGlobalMonitor;
  const [sourceOrdersResult, refreshSourceOrders] = useQuery({
    query: TTradeSourceOrdersQuery,
    variables: {
      accountId,
      startDate: sourceStartDate,
      endDate: sourceEndDate,
    },
    pause: !accountId || !showExternalEntry,
    requestPolicy: 'network-only',
  });
  const importedOrderIds = new Set(
    (sourceOrdersResult.data?.tTradeImportedEntries || []).flatMap(item =>
      item.sourceOrderId ? [item.sourceOrderId] : []
    )
  );
  const sourceBuyOrders = (sourceOrdersResult.data?.historyOrders || [])
    .filter(
      order =>
        String(order.type) === 'BUY' &&
        String(order.status) === 'SUCCEEDED' &&
        order.tradedVolume > 0
    )
    .sort((a, b) => Date.parse(b.time) - Date.parse(a.time));

  const handleSourceOrdersRefresh = React.useCallback(
    async (showSuccessToast = true) => {
      if (!accountId) return;
      const now = Date.now();
      if (now - lastSourceOrdersSyncRequestRef.current < 3_000) return;
      lastSourceOrdersSyncRequestRef.current = now;
      setSourceOrdersSyncError('');
      const result = await syncSourceOrders({ accountId });
      const payload = result.data?.syncTTradeSourceOrders;
      const errorMessage =
        payload?.message || result.error?.message || '同步当日委托失败';
      if (!payload?.success) {
        setSourceOrdersSyncError(errorMessage);
        toast({
          title: '当日委托同步失败',
          description: errorMessage,
          variant: 'destructive',
        });
        return;
      }
      setSourceOrdersSyncedAt(new Date());
      refreshSourceOrders({ requestPolicy: 'network-only' });
      if (showSuccessToast) {
        toast({
          title: '当日委托已同步',
          description: payload.message,
        });
      }
    },
    [accountId, refreshSourceOrders, syncSourceOrders, toast]
  );

  React.useEffect(() => {
    if (!showExternalEntry || !accountId) return;
    if (autoSyncedSourceOrdersAccountRef.current === accountId) return;
    autoSyncedSourceOrdersAccountRef.current = accountId;
    void handleSourceOrdersRefresh(false);
  }, [accountId, handleSourceOrdersRefresh, showExternalEntry]);

  React.useEffect(() => {
    if (!accountId) return;

    const refreshIfVisible = () => {
      if (document.visibilityState !== 'visible') return;
      const now = Date.now();
      if (now - lastMonitorRefreshRequestRef.current < 5_000) return;
      lastMonitorRefreshRequestRef.current = now;
      refreshMonitor({ requestPolicy: 'network-only' });
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') refreshIfVisible();
    };

    const timer = window.setInterval(refreshIfVisible, 30_000);
    window.addEventListener('focus', refreshIfVisible);
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', refreshIfVisible);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [accountId, refreshMonitor]);

  React.useEffect(() => {
    if (!monitorResult.data || monitorResult.fetching) return;
    setLastMonitorRefreshAt(new Date());
    setManualRefreshPending(false);
  }, [monitorResult.data, monitorResult.fetching]);

  const handleMonitorRefresh = React.useCallback(() => {
    if (!accountId) return;
    lastMonitorRefreshRequestRef.current = Date.now();
    setManualRefreshPending(true);
    refreshMonitor({ requestPolicy: 'network-only' });
  }, [accountId, refreshMonitor]);

  React.useEffect(() => {
    if (!monitor) return;
    const hydrationKey = `${monitor.accountId}:${monitor.configVersion}`;
    if (hydratedVersionRef.current === hydrationKey) return;
    hydratedVersionRef.current = hydrationKey;
    setForm({
      mode: monitor.mode === 'live' ? 'live' : 'paper',
      acknowledged: monitor.autoExitAcknowledged,
      targetTradeAmount: String(monitor.targetTradeAmount),
      maxTradeAmount: String(monitor.maxTradeAmount),
      maxConcurrentBatches: String(monitor.maxConcurrentBatches),
      maxTotalTExposurePct: String(monitor.maxTotalTExposurePct * 100),
      targetProfitPct: String(monitor.targetProfitPct),
      baseFloorPct: String(monitor.baseFloorPct),
      initialGapPct: String(monitor.initialGapPct),
      trailingGapSlope: String(monitor.trailingGapSlope),
      maxGapPct: String(monitor.maxGapPct),
      hardStopEnabled: monitor.hardStopEnabled,
      hardStopPct: String(monitor.hardStopPct),
      signalLookbackSeconds: String(monitor.signalLookbackSeconds),
      stabilizationSeconds: String(monitor.stabilizationSeconds),
      pullbackThresholdPct: String(monitor.pullbackThresholdPct),
      reboundThresholdPct: String(monitor.reboundThresholdPct),
      maxSpreadTicks: String(monitor.maxSpreadTicks),
      approvalTtlSeconds: String(monitor.approvalTtlSeconds),
      maxPriceDeviationPct: String(monitor.maxPriceDeviationPct),
      timeExitMode: monitor.timeExitMode,
      timeExitTime: monitor.timeExitTime,
      maxHoldingTradingDays: String(monitor.maxHoldingTradingDays),
      cooldownSeconds: String(monitor.cooldownSeconds),
    });
    setIgnoredCodes([...monitor.ignoredStockCodes]);
  }, [monitor]);

  const setField = React.useCallback(
    <K extends keyof SettingsForm>(key: K, value: SettingsForm[K]) => {
      setForm(current => ({ ...current, [key]: value }));
    },
    []
  );

  const persist = React.useCallback(
    async (enabled: boolean, nextIgnored = ignoredCodes) => {
      if (!accountId) return false;
      const result = await saveMonitor({
        input: {
          accountId,
          enabled,
          mode: form.mode,
          autoExitAcknowledged:
            form.mode === 'paper' ? false : form.acknowledged,
          ignoredStockCodes: nextIgnored,
          targetTradeAmount: numberValue(form.targetTradeAmount, 10000),
          maxTradeAmount: numberValue(form.maxTradeAmount, 12000),
          maxConcurrentBatches: integerValue(form.maxConcurrentBatches, 3),
          maxTotalTExposurePct:
            numberValue(form.maxTotalTExposurePct, 10) / 100,
          signalLookbackSeconds: integerValue(form.signalLookbackSeconds, 300),
          stabilizationSeconds: integerValue(form.stabilizationSeconds, 15),
          pullbackThresholdPct: numberValue(form.pullbackThresholdPct, 0.8),
          reboundThresholdPct: numberValue(form.reboundThresholdPct, 0.2),
          maxSpreadTicks: integerValue(form.maxSpreadTicks, 3),
          approvalTtlSeconds: integerValue(form.approvalTtlSeconds, 30),
          maxPriceDeviationPct: numberValue(form.maxPriceDeviationPct, 0.3),
          targetProfitPct: numberValue(form.targetProfitPct, 2),
          baseFloorPct: numberValue(form.baseFloorPct, 0.5),
          initialGapPct: numberValue(form.initialGapPct, 1.5),
          trailingGapSlope: numberValue(form.trailingGapSlope, 0.25),
          maxGapPct: numberValue(form.maxGapPct, 3),
          hardStopEnabled: form.hardStopEnabled,
          hardStopPct: numberValue(form.hardStopPct, -0.8),
          timeExitMode: form.timeExitMode,
          timeExitTime: form.timeExitTime,
          maxHoldingTradingDays: integerValue(form.maxHoldingTradingDays, 5),
          cooldownSeconds: integerValue(form.cooldownSeconds, 300),
        },
      });
      const payload = result.data?.saveTTradeGlobalMonitor;
      const success = Boolean(payload?.success);
      toast({
        title: success ? '全局做 T 设置已更新' : '设置未保存',
        description: payload?.message || result.error?.message || '请求失败',
        variant: success ? 'default' : 'destructive',
      });
      if (success) refreshMonitor({ requestPolicy: 'network-only' });
      return success;
    },
    [accountId, form, ignoredCodes, refreshMonitor, saveMonitor, toast]
  );

  const handleIgnore = async (stockCode: string, ignored: boolean) => {
    const previous = ignoredCodes;
    const next = ignored
      ? Array.from(new Set([...ignoredCodes, stockCode]))
      : ignoredCodes.filter(code => code !== stockCode);
    setIgnoredCodes(next);
    if (!(await persist(Boolean(monitor?.enabled), next))) {
      setIgnoredCodes(previous);
    }
  };

  const handleAddIgnore = async () => {
    const value = ignoreInput.trim().toUpperCase();
    if (!value) return;
    const previous = ignoredCodes;
    const next = Array.from(new Set([...ignoredCodes, value]));
    setIgnoredCodes(next);
    setIgnoreInput('');
    if (!(await persist(Boolean(monitor?.enabled), next))) {
      setIgnoredCodes(previous);
    }
  };

  const handleReconcile = async () => {
    if (!accountId) return;
    const result = await reconcileMonitor({ accountId });
    const payload = result.data?.reconcileTTradeGlobalMonitor;
    toast({
      title: payload?.success ? '持仓已同步' : '同步未完成',
      description: payload?.message || result.error?.message || '请求失败',
      variant: payload?.success ? 'default' : 'destructive',
    });
    refreshMonitor({ requestPolicy: 'network-only' });
  };

  const handleSignal = async (
    action: 'approve' | 'reject',
    runId: string,
    intentId: string
  ) => {
    let payload: { message: string; success: boolean } | undefined;
    let errorMessage = '';
    if (action === 'approve') {
      const result = await approveEntry({ runId, intentId });
      payload = result.data?.approveTTradeEntry;
      errorMessage = result.error?.message || '';
    } else {
      const result = await rejectEntry({ runId, intentId });
      payload = result.data?.rejectTTradeEntry;
      errorMessage = result.error?.message || '';
    }
    toast({
      title: payload?.success ? '信号已处理' : '信号未执行',
      description: payload?.message || errorMessage || '请求失败',
      variant: payload?.success ? 'default' : 'destructive',
    });
    refreshMonitor({ requestPolicy: 'network-only' });
  };

  const handleImportExternalEntry = async () => {
    if (!monitor?.strategyRunId || !selectedOrderId) return;
    const result = await importExternalEntry({
      input: {
        runId: monitor.strategyRunId,
        accountId,
        orderId: selectedOrderId,
      },
    });
    const payload = result.data?.importTTradeExternalEntry;
    toast({
      title: payload?.success ? '外部成交已纳入监控' : '外部成交未导入',
      description: payload?.message || result.error?.message || '请求失败',
      variant: payload?.success ? 'default' : 'destructive',
    });
    if (payload?.success) {
      setShowExternalEntry(false);
      setSelectedOrderId('');
      setExternalAcknowledged(false);
      refreshMonitor({ requestPolicy: 'network-only' });
    }
  };

  const pendingSessions = (monitor?.sessions || []).filter(
    session => session.pendingEntryIntentId
  );
  const actionLoading =
    saveResult.fetching ||
    reconcileResult.fetching ||
    approveResult.fetching ||
    rejectResult.fetching ||
    importResult.fetching ||
    syncSourceOrdersResult.fetching;

  const sidebar = (
    <aside className="flex h-full min-h-0 flex-col bg-[#0b1628]">
      <div className="flex h-[68px] shrink-0 items-center justify-between border-b border-white/[0.05] px-4">
        <div>
          <div className="text-[10px] font-black uppercase tracking-[0.24em] text-red-300">
            T Trade
          </div>
          <h1 className="mt-1 text-base font-black text-slate-100">做T助手</h1>
        </div>
        <button
          type="button"
          aria-label="刷新做 T 监控"
          disabled={!accountId || actionLoading}
          onClick={handleMonitorRefresh}
          className="flex h-8 w-8 items-center justify-center rounded-md border border-white/[0.07] text-slate-500 transition-colors hover:border-red-400/25 hover:text-red-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/60 disabled:opacity-40"
        >
          <RefreshCw
            className={cn('h-4 w-4', manualRefreshPending && 'animate-spin')}
          />
        </button>
      </div>

      <div className="grid shrink-0 grid-cols-2 gap-2 border-b border-white/[0.05] p-3">
        <MetricCard
          icon={Radar}
          label="合格持仓"
          value={monitor?.eligibleCount ?? 0}
          tone="red"
        />
        <MetricCard
          icon={Activity}
          label="待确认"
          value={monitor?.pendingSignalCount ?? 0}
          tone={monitor?.pendingSignalCount ? 'amber' : 'slate'}
        />
        <MetricCard
          icon={TrendingUp}
          label="活跃批次"
          value={monitor?.activeBatchCount ?? 0}
          tone={monitor?.activeBatchCount ? 'emerald' : 'slate'}
        />
        <MetricCard
          icon={Ban}
          label="忽略 / 退出"
          value={`${monitor?.ignoredCount ?? 0} / ${monitor?.drainingCount ?? 0}`}
        />
      </div>

      <div className="flex h-10 shrink-0 items-center justify-between border-b border-white/[0.05] px-4 text-[10px] font-black uppercase tracking-[0.14em] text-slate-500">
        <span>动态持仓</span>
        <span className="font-mono text-slate-600">
          {monitor?.holdings.length ?? 0} 只
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto custom-scrollbar">
        {(monitor?.holdings || []).map(holding => {
          const presentation =
            statusPresentation[holding.status] || statusPresentation.STOPPED;
          return (
            <div
              key={holding.stockCode}
              className="border-b border-white/[0.04] px-4 py-3 transition-colors hover:bg-white/[0.025]"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <InstrumentNameLabel
                    className="truncate text-xs font-black text-slate-200"
                    stockCode={holding.stockCode}
                    knownName={resolveInstrumentName(
                      holding.stockCode,
                      positionNamesByCode.get(holding.stockCode.toUpperCase()),
                      holding.instrumentName
                    )}
                  />
                  <div className="mt-0.5 font-mono text-[10px] text-slate-600">
                    {holding.stockCode}
                  </div>
                </div>
                <span
                  className={cn(
                    'shrink-0 border px-1.5 py-0.5 text-[9px] font-black',
                    presentation.className
                  )}
                >
                  {presentation.label}
                </span>
              </div>
              <div className="mt-2 flex items-center justify-between font-mono text-[10px] text-slate-500">
                <span>
                  {holding.availableVolume.toLocaleString()} /{' '}
                  {holding.volume.toLocaleString()} 股
                </span>
                <span
                  className={cn(
                    'font-bold',
                    (holding.session?.lastNetProfitPct || 0) >= 0
                      ? 'text-red-300'
                      : 'text-emerald-300'
                  )}
                >
                  {holding.session?.activeVolume
                    ? `${formatNumber(holding.session.lastNetProfitPct)}%`
                    : '--'}
                </span>
              </div>
            </div>
          );
        })}

        {!monitorResult.fetching && (monitor?.holdings.length || 0) === 0 && (
          <div className="flex h-40 flex-col items-center justify-center px-6 text-center">
            <Radar className="h-8 w-8 text-slate-700" />
            <div className="mt-3 text-xs font-bold text-slate-500">
              暂无持仓监控对象
            </div>
            <div className="mt-1 text-[10px] leading-4 text-slate-700">
              同步账户持仓后自动生成标的范围
            </div>
          </div>
        )}
      </div>

      <div className="shrink-0 border-t border-white/[0.06] bg-[#091322] p-3">
        <Label
          htmlFor="t-trade-account"
          className="mb-1.5 block text-[10px] font-black uppercase tracking-[0.12em] text-slate-600"
        >
          监控账户
        </Label>
        <Select value={accountId} onValueChange={setAccountId}>
          <SelectTrigger
            id="t-trade-account"
            className="h-10 rounded-sm border-white/[0.08] bg-white/[0.025] font-mono text-xs focus:ring-red-500/60"
          >
            <SelectValue placeholder="选择账户" />
          </SelectTrigger>
          <SelectContent>
            {accounts.map(account => (
              <SelectItem key={account} value={account}>
                {account}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </aside>
  );

  const replaySidebar = (
    <aside className="flex h-full min-h-0 flex-col bg-[#0b1628]">
      <div className="flex h-[68px] shrink-0 items-center border-b border-white/[0.05] px-4">
        <div>
          <div className="text-[10px] font-black uppercase tracking-[0.24em] text-cyan-300">
            Replay Lab
          </div>
          <h1 className="mt-1 text-base font-black text-slate-100">回放测试</h1>
        </div>
      </div>
      <div className="border-b border-white/[0.05] p-4">
        <div className="flex items-center gap-2 text-xs font-black text-cyan-100">
          <ShieldCheck className="h-4 w-4 text-cyan-300" />
          隔离回测环境
        </div>
        <p className="mt-2 text-[10px] leading-5 text-slate-600">
          回放使用 BACKTEST
          Broker，测试信号自动确认；实时监控保持原状态，不会提交实盘委托。
        </p>
      </div>
      <div className="space-y-3 p-4 text-[10px] text-slate-500">
        <div className="flex items-start gap-2">
          <History className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-600" />
          以开始日前最近日结快照作为初始现金和持仓。
        </div>
        <div className="flex items-start gap-2">
          <BarChart3 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-600" />
          同时计算账户曲线与不做 T 被动基准。
        </div>
        <div className="flex items-start gap-2">
          <CircleDollarSign className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-600" />
          收益已扣佣金、过户费、卖出印花税与滑点。
        </div>
      </div>
      <div className="mt-auto shrink-0 border-t border-white/[0.06] bg-[#091322] p-3">
        <Label
          htmlFor="t-trade-replay-account"
          className="mb-1.5 block text-[10px] font-black uppercase tracking-[0.12em] text-slate-600"
        >
          回放账户
        </Label>
        <Select value={accountId} onValueChange={setAccountId}>
          <SelectTrigger
            id="t-trade-replay-account"
            className="h-10 rounded-sm border-white/[0.08] bg-white/[0.025] font-mono text-xs focus:ring-cyan-500/60"
          >
            <SelectValue placeholder="选择账户" />
          </SelectTrigger>
          <SelectContent>
            {accounts.map(account => (
              <SelectItem key={account} value={account}>
                {account}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </aside>
  );

  const toolbar = (
    <div className="flex h-12 shrink-0 items-center justify-between gap-3 overflow-hidden border-b border-white/[0.05] bg-[#07111f]/95 px-4">
      <nav
        className="flex h-full min-w-0 items-stretch"
        aria-label="做 T 工作区"
      >
        {(['REALTIME', 'REPLAY'] as const).map(mode => {
          const active = workspaceMode === mode;
          return (
            <button
              key={mode}
              type="button"
              onClick={() => setWorkspaceMode(mode)}
              className={cn(
                'relative flex h-full shrink-0 cursor-pointer items-center gap-1.5 px-3 text-[11px] font-black transition-colors after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset',
                active
                  ? mode === 'REPLAY'
                    ? 'text-cyan-200 after:bg-cyan-400 focus-visible:ring-cyan-400/60'
                    : 'text-red-200 after:bg-red-400 focus-visible:ring-red-500/60'
                  : 'text-slate-600 hover:text-slate-200'
              )}
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
        {workspaceMode === 'REALTIME' && (
          <>
            <span className="mx-2 my-3 w-px bg-white/[0.08]" />
            {tTradeModes.map(mode => {
              const isActive = activeMode === mode.id;
              return (
                <button
                  key={mode.id}
                  type="button"
                  onClick={() => setActiveMode(mode.id as TTradeStudioMode)}
                  className={cn(
                    'relative h-full shrink-0 cursor-pointer px-3 text-xs font-bold transition-colors after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-red-500/60',
                    isActive
                      ? 'text-red-200 after:bg-red-400'
                      : 'text-slate-500 hover:text-slate-200'
                  )}
                >
                  {mode.label}
                  {mode.id === 'SIGNALS' && Boolean(pendingSessions.length) && (
                    <span className="ml-1.5 rounded-sm bg-amber-400/15 px-1.5 py-0.5 font-mono text-[9px] text-amber-200">
                      {pendingSessions.length}
                    </span>
                  )}
                </button>
              );
            })}
          </>
        )}
      </nav>

      <div className="flex shrink-0 items-center gap-2">
        {workspaceMode === 'REPLAY' ? (
          <span className="hidden items-center gap-1.5 text-[10px] font-bold text-cyan-200 sm:inline-flex">
            <ShieldCheck className="h-3.5 w-3.5" />
            隔离回测 · 自动确认测试信号
          </span>
        ) : (
          <>
            <span
              className={cn(
                'hidden items-center gap-1.5 text-[10px] font-bold md:inline-flex',
                monitor?.enabled ? 'text-emerald-300' : 'text-slate-600'
              )}
            >
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  monitor?.enabled
                    ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.65)]'
                    : 'bg-slate-700'
                )}
              />
              {monitor?.enabled ? '全局监控运行中' : '全局监控已停止'}
            </span>
            <span className="hidden h-4 w-px bg-white/[0.08] sm:block" />
            <button
              type="button"
              disabled={!accountId || actionLoading}
              onClick={handleReconcile}
              className="flex h-8 items-center gap-1.5 px-2 text-[10px] font-bold text-slate-500 transition-colors hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/60 disabled:opacity-40"
            >
              <RefreshCw
                className={cn(
                  'h-3.5 w-3.5',
                  reconcileResult.fetching && 'animate-spin'
                )}
              />
              <span className="hidden sm:inline">同步持仓</span>
            </button>
            <Button
              type="button"
              size="sm"
              className={cn(
                'h-8 rounded-sm px-3 text-[10px] font-black',
                monitor?.enabled
                  ? 'bg-slate-700 text-slate-100 hover:bg-slate-600'
                  : 'bg-red-500 text-white hover:bg-red-400'
              )}
              disabled={
                !accountId ||
                actionLoading ||
                (!monitor?.enabled &&
                  form.mode === 'live' &&
                  !form.acknowledged)
              }
              onClick={() => persist(!monitor?.enabled)}
            >
              {saveResult.fetching ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : monitor?.enabled ? (
                <Square className="mr-1.5 h-3 w-3" />
              ) : (
                <Play className="mr-1.5 h-3.5 w-3.5" />
              )}
              {monitor?.enabled ? '停止监控' : '启动监控'}
            </Button>
          </>
        )}
      </div>
    </div>
  );

  const monitorView = (
    <div className="flex h-full min-h-0 flex-col bg-[#0a1424]">
      {accounts.length === 0 && (
        <div className="flex shrink-0 items-center gap-2 border-b border-amber-400/15 bg-amber-400/[0.07] px-4 py-2.5 text-xs font-bold text-amber-100">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          当前没有可识别的持仓账户，请先完成账户持仓同步。
        </div>
      )}
      {(monitorResult.error || monitor?.lastError) && (
        <div className="flex shrink-0 items-center gap-2 border-b border-rose-400/15 bg-rose-500/[0.07] px-4 py-2.5 text-xs text-rose-100">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>{monitorResult.error?.message || monitor?.lastError}</span>
        </div>
      )}
      {pendingSessions.length > 0 && (
        <button
          type="button"
          onClick={() => setActiveMode('SIGNALS')}
          className="flex shrink-0 items-center justify-between border-b border-amber-400/15 bg-amber-400/[0.05] px-4 py-2.5 text-left transition-colors hover:bg-amber-400/[0.08] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-amber-400/50"
        >
          <span className="inline-flex items-center gap-2 text-xs font-bold text-amber-100">
            <Activity className="h-4 w-4" />有 {pendingSessions.length}{' '}
            个买入机会等待人工确认
          </span>
          <span className="text-[10px] font-bold text-amber-300">
            查看信号 →
          </span>
        </button>
      )}

      <div className="flex shrink-0 items-center justify-between border-b border-white/[0.05] px-4 py-3">
        <div>
          <h2 className="text-sm font-black text-slate-100">
            全部持仓监控范围
          </h2>
          <p className="mt-0.5 text-[10px] text-slate-600">
            账户持仓动态生成 · A 股代码且昨日可用库存不少于 100 股时自动纳入
          </p>
        </div>
        <div className="flex items-center gap-3 text-[10px] font-bold text-slate-600">
          <span>{monitor?.mode === 'live' ? '实盘执行' : '模拟观察'}</span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 rounded-sm border-white/10 px-2.5 text-[10px] text-slate-300"
            disabled={!accountId || actionLoading}
            onClick={() => setShowExternalEntry(value => !value)}
          >
            <Plus className="mr-1.5 h-3.5 w-3.5" />
            从已成委托选择
          </Button>
          {monitorResult.fetching && !monitor ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-red-300" />
          ) : lastMonitorRefreshAt ? (
            <span
              className="font-mono font-normal text-slate-700"
              title={`最近同步：${lastMonitorRefreshAt.toLocaleString('zh-CN', {
                hour12: false,
              })}`}
            >
              {lastMonitorRefreshAt.toLocaleTimeString('zh-CN', {
                hour12: false,
              })}
            </span>
          ) : null}
        </div>
      </div>

      {showExternalEntry && (
        <section className="shrink-0 border-b border-amber-400/15 bg-[#0b1628] px-4 py-3">
          {!monitor?.enabled || !monitor.strategyRunId ? (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-start gap-2.5">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
                <div>
                  <div className="text-xs font-black text-amber-100">
                    请先启动全局监控
                  </div>
                  <p className="mt-1 text-[10px] leading-4 text-slate-500">
                    外部成交需要加入一个正在运行的做 T
                    策略，才能持续读取行情并触发自动卖出。
                  </p>
                </div>
              </div>
              <Button
                type="button"
                size="sm"
                className="h-8 shrink-0 rounded-sm bg-red-500 px-3 text-[10px] font-black text-white hover:bg-red-400"
                disabled={
                  actionLoading || (form.mode === 'live' && !form.acknowledged)
                }
                onClick={() => persist(true)}
              >
                <Play className="mr-1.5 h-3.5 w-3.5" />
                启动监控后添加
              </Button>
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-xs font-black text-slate-100">
                    选择已成交买入委托
                  </div>
                  <p className="mt-1 text-[10px] text-slate-500">
                    先同步 miniQMT
                    当日委托，再读取委托表；每个已成委托只能建立一次自动卖出批次。
                  </p>
                  {sourceOrdersSyncedAt && !sourceOrdersSyncError && (
                    <p className="mt-1 font-mono text-[9px] text-emerald-500/70">
                      当日委托已同步 ·{' '}
                      {sourceOrdersSyncedAt.toLocaleTimeString('zh-CN', {
                        hour12: false,
                      })}
                    </p>
                  )}
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-8 text-[10px] text-slate-400"
                  disabled={syncSourceOrdersResult.fetching}
                  onClick={() => void handleSourceOrdersRefresh()}
                >
                  <RefreshCw
                    className={cn(
                      'mr-1.5 h-3.5 w-3.5',
                      (syncSourceOrdersResult.fetching ||
                        sourceOrdersResult.fetching) &&
                        'animate-spin'
                    )}
                  />
                  {syncSourceOrdersResult.fetching ? '同步中' : '同步并刷新'}
                </Button>
              </div>
              {sourceOrdersSyncError && (
                <div className="mt-3 flex items-start gap-2 border border-red-500/20 bg-red-500/[0.06] px-3 py-2 text-[10px] leading-4 text-red-200">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-400" />
                  <span>
                    {sourceOrdersSyncError}。当前仍显示委托表中的已有记录。
                  </span>
                </div>
              )}
              <div className="mt-3 flex items-end gap-2">
                <div>
                  <Label
                    htmlFor="t-trade-source-start"
                    className="text-[10px] text-slate-500"
                  >
                    开始日期
                  </Label>
                  <Input
                    id="t-trade-source-start"
                    type="date"
                    value={sourceStartDate}
                    onChange={event => setSourceStartDate(event.target.value)}
                    className="mt-1 h-8 w-36 rounded-sm border-white/10 bg-[#07111f] text-[10px]"
                  />
                </div>
                <div>
                  <Label
                    htmlFor="t-trade-source-end"
                    className="text-[10px] text-slate-500"
                  >
                    结束日期
                  </Label>
                  <Input
                    id="t-trade-source-end"
                    type="date"
                    value={sourceEndDate}
                    onChange={event => setSourceEndDate(event.target.value)}
                    className="mt-1 h-8 w-36 rounded-sm border-white/10 bg-[#07111f] text-[10px]"
                  />
                </div>
              </div>
              <div className="mt-3 max-h-52 overflow-y-auto border border-white/[0.07] custom-scrollbar">
                {sourceBuyOrders.map(order => {
                  const holding = monitor.holdings.find(
                    item => item.stockCode === order.stockCode
                  );
                  const imported = importedOrderIds.has(order.id);
                  const unavailable =
                    imported ||
                    !holding ||
                    Boolean(holding.session?.activeVolume) ||
                    order.tradedVolume % 100 !== 0;
                  return (
                    <label
                      key={order.id}
                      className={cn(
                        'grid grid-cols-[24px_minmax(140px,1fr)_100px_100px_80px] items-center border-b border-white/[0.05] px-3 py-2 text-[10px] last:border-b-0',
                        unavailable
                          ? 'cursor-not-allowed opacity-45'
                          : 'cursor-pointer hover:bg-white/[0.03]'
                      )}
                    >
                      <input
                        type="radio"
                        name="t-trade-filled-buy-order"
                        value={order.id}
                        checked={selectedOrderId === order.id}
                        disabled={unavailable}
                        onChange={() => setSelectedOrderId(order.id)}
                        className="h-3.5 w-3.5 accent-amber-400"
                      />
                      <span>
                        <span className="font-bold text-slate-200">
                          {holding?.instrumentName || order.stockCode}
                        </span>
                        <span className="ml-2 font-mono text-slate-600">
                          {order.stockCode}
                        </span>
                      </span>
                      <span className="text-right font-mono text-slate-300">
                        {order.tradedVolume.toLocaleString()} 股
                      </span>
                      <span className="text-right font-mono text-slate-300">
                        ¥{formatNumber(order.tradedPrice, 3)}
                      </span>
                      <span
                        className={cn(
                          'text-right font-mono',
                          imported
                            ? 'font-bold text-emerald-400'
                            : 'text-slate-600'
                        )}
                      >
                        {imported
                          ? '已纳入'
                          : new Date(order.time).toLocaleDateString('zh-CN')}
                      </span>
                    </label>
                  );
                })}
                {!sourceOrdersResult.fetching &&
                  sourceBuyOrders.length === 0 && (
                    <div className="px-3 py-6 text-center text-[10px] text-slate-600">
                      所选日期范围内没有已成交买入委托
                    </div>
                  )}
              </div>
              <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <label className="flex cursor-pointer items-start gap-2 text-[10px] leading-4 text-amber-100">
                  <input
                    type="checkbox"
                    checked={externalAcknowledged}
                    onChange={event =>
                      setExternalAcknowledged(event.target.checked)
                    }
                    className="mt-0.5 h-3.5 w-3.5 accent-amber-400"
                  />
                  我确认将所选已成交买入委托纳入当前已启用的自动退出规则。
                </label>
                <Button
                  type="button"
                  className="h-9 rounded-sm bg-amber-500 px-4 text-xs font-black text-slate-950 hover:bg-amber-400"
                  disabled={
                    !selectedOrderId ||
                    !externalAcknowledged ||
                    importResult.fetching
                  }
                  onClick={handleImportExternalEntry}
                >
                  {importResult.fetching && (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  )}
                  纳入自动卖出
                </Button>
              </div>
            </>
          )}
        </section>
      )}

      <div className="min-h-0 flex-1 overflow-auto custom-scrollbar">
        <table className="w-full min-w-[780px] text-left text-xs">
          <thead className="sticky top-0 z-10 bg-[#0b1628] text-[10px] font-black uppercase tracking-[0.1em] text-slate-600 shadow-[0_1px_0_rgba(255,255,255,0.05)]">
            <tr>
              <th className="px-4 py-2.5">标的</th>
              <th className="px-3 py-2.5 text-right">持仓 / 可用</th>
              <th className="px-3 py-2.5">状态</th>
              <th className="px-3 py-2.5 text-right">活跃仓</th>
              <th className="px-3 py-2.5 text-right">本批净收益</th>
              <th className="px-3 py-2.5 text-right">完成次数</th>
              <th className="px-4 py-2.5 text-right">范围设置</th>
            </tr>
          </thead>
          <tbody>
            {(monitor?.holdings || []).map(holding => {
              const presentation =
                statusPresentation[holding.status] ||
                statusPresentation.STOPPED;
              return (
                <tr
                  key={holding.stockCode}
                  className="border-b border-white/[0.04] transition-colors hover:bg-white/[0.025]"
                >
                  <td className="px-4 py-3">
                    <InstrumentNameLabel
                      className="font-black text-slate-100"
                      stockCode={holding.stockCode}
                      knownName={resolveInstrumentName(
                        holding.stockCode,
                        positionNamesByCode.get(
                          holding.stockCode.toUpperCase()
                        ),
                        holding.instrumentName
                      )}
                    />
                    <div className="mt-0.5 font-mono text-[10px] text-slate-600">
                      {holding.stockCode}
                    </div>
                  </td>
                  <td className="px-3 py-3 text-right font-mono tabular-nums text-slate-300">
                    {holding.volume.toLocaleString()}
                    <span className="mx-1 text-slate-700">/</span>
                    {holding.availableVolume.toLocaleString()}
                  </td>
                  <td className="px-3 py-3">
                    <span
                      className={cn(
                        'inline-flex border px-2 py-0.5 text-[10px] font-black',
                        presentation.className
                      )}
                    >
                      {presentation.label}
                    </span>
                    <div
                      className="mt-1 max-w-[260px] truncate text-[10px] text-slate-600"
                      title={holding.reason}
                    >
                      {holding.reason}
                    </div>
                  </td>
                  <td className="px-3 py-3 text-right font-mono text-slate-400">
                    {holding.session?.activeVolume?.toLocaleString() || '--'}
                  </td>
                  <td
                    className={cn(
                      'px-3 py-3 text-right font-mono font-bold tabular-nums',
                      (holding.session?.lastNetProfitPct || 0) >= 0
                        ? 'text-red-300'
                        : 'text-emerald-300'
                    )}
                  >
                    {holding.session?.activeVolume
                      ? `${formatNumber(holding.session.lastNetProfitPct)}%`
                      : '--'}
                  </td>
                  <td className="px-3 py-3 text-right font-mono text-slate-400">
                    {holding.session?.completedCycles ?? 0}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className={cn(
                        'h-7 rounded-sm px-2 text-[10px]',
                        holding.ignored
                          ? 'text-red-300 hover:text-red-200'
                          : 'text-slate-500 hover:text-rose-200'
                      )}
                      disabled={actionLoading}
                      onClick={() =>
                        handleIgnore(holding.stockCode, !holding.ignored)
                      }
                    >
                      {holding.ignored ? '恢复监控' : '忽略股票'}
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {!monitorResult.fetching && (monitor?.holdings.length || 0) === 0 && (
          <div className="flex min-h-64 flex-col items-center justify-center text-center">
            <Radar className="h-10 w-10 text-slate-800" />
            <div className="mt-3 text-sm font-bold text-slate-500">
              该账户暂无可监控持仓
            </div>
            <div className="mt-1 text-[10px] text-slate-700">
              做 T 标的由账户持仓动态生成，不需要逐只启动
            </div>
          </div>
        )}
      </div>

      <div className="grid shrink-0 gap-px border-t border-white/[0.05] bg-white/[0.05] text-[10px] leading-4 text-slate-600 md:grid-cols-3">
        <div className="flex items-start gap-2 bg-[#091322] px-4 py-2.5">
          <Activity className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-400" />
          买入机会人工确认后，仍重新校验价格、资金和合法数量。
        </div>
        <div className="flex items-start gap-2 bg-[#091322] px-4 py-2.5">
          <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
          动态持仓在同一策略运行中隔离状态，成交真源来自券商回报。
        </div>
        <div className="flex items-start gap-2 bg-[#091322] px-4 py-2.5">
          <TrendingUp className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" />
          忽略或停用时，活跃 T 批次先完成退出再停止会话。
        </div>
      </div>
    </div>
  );

  const signalsView = (
    <div className="flex h-full min-h-0 flex-col bg-[#0a1424]">
      <div className="shrink-0 border-b border-white/[0.05] px-4 py-3">
        <h2 className="text-sm font-black text-slate-100">待确认买入机会</h2>
        <p className="mt-0.5 text-[10px] text-slate-600">
          Tick 信号仅是候选机会，确认时会再次执行交易规则与风控校验
        </p>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4 custom-scrollbar">
        {pendingSessions.length === 0 ? (
          <div className="flex h-full min-h-64 flex-col items-center justify-center text-center">
            <Activity className="h-10 w-10 text-slate-800" />
            <div className="mt-3 text-sm font-bold text-slate-500">
              当前没有待确认信号
            </div>
            <div className="mt-1 text-[10px] text-slate-700">
              全局监控运行后会自动扫描全部合格持仓
            </div>
          </div>
        ) : (
          <div className="grid gap-px overflow-hidden border border-white/[0.06] bg-white/[0.06] xl:grid-cols-2">
            {pendingSessions.map(session => {
              const signal = (session.currentSignal || {}) as Record<
                string,
                unknown
              >;
              return (
                <article
                  key={`${session.runId}:${session.stockCode}`}
                  className="flex min-h-32 flex-col justify-between bg-[#0b1628] p-4"
                >
                  <div>
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-mono text-sm font-black text-slate-100">
                        {session.stockCode}
                      </span>
                      <span className="border border-amber-400/20 bg-amber-400/[0.08] px-2 py-0.5 text-[9px] font-black text-amber-200">
                        等待确认
                      </span>
                    </div>
                    <div className="mt-3 grid grid-cols-4 gap-3 font-mono text-[10px]">
                      <div>
                        <div className="text-slate-600">信号价</div>
                        <div className="mt-1 text-slate-300">
                          {formatNumber(Number(signal.signal_price || 0), 3)}
                        </div>
                      </div>
                      <div>
                        <div className="text-slate-600">回撤</div>
                        <div className="mt-1 text-emerald-300">
                          {formatNumber(Number(signal.pullback_pct || 0))}%
                        </div>
                      </div>
                      <div>
                        <div className="text-slate-600">反弹</div>
                        <div className="mt-1 text-red-300">
                          {formatNumber(Number(signal.rebound_pct || 0))}%
                        </div>
                      </div>
                      <div>
                        <div className="text-slate-600">计划数量</div>
                        <div className="mt-1 text-slate-300">
                          {session.plannedEntryVolume.toLocaleString()} 股
                        </div>
                      </div>
                    </div>
                  </div>
                  <div className="mt-4 flex justify-end gap-2 border-t border-white/[0.05] pt-3">
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="h-8 rounded-sm text-[10px] text-slate-500 hover:text-slate-200"
                      disabled={actionLoading}
                      onClick={() =>
                        handleSignal(
                          'reject',
                          session.runId,
                          session.pendingEntryIntentId!
                        )
                      }
                    >
                      <X className="mr-1.5 h-3.5 w-3.5" />
                      忽略本次
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      className="h-8 rounded-sm bg-red-500 text-[10px] text-white hover:bg-red-400"
                      disabled={actionLoading}
                      onClick={() =>
                        handleSignal(
                          'approve',
                          session.runId,
                          session.pendingEntryIntentId!
                        )
                      }
                    >
                      <Check className="mr-1.5 h-3.5 w-3.5" />
                      确认买入
                    </Button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );

  const settingsView = (
    <div className="flex h-full min-h-0 flex-col bg-[#0a1424]">
      <div className="flex shrink-0 items-center justify-between border-b border-white/[0.05] px-4 py-3">
        <div>
          <h2 className="text-sm font-black text-slate-100">全局策略参数</h2>
          <p className="mt-0.5 text-[10px] text-slate-600">
            对账户内所有未忽略的合格持仓统一生效
          </p>
        </div>
        <span className="font-mono text-[10px] text-slate-600">
          配置版本 v{monitor?.configVersion ?? 0}
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto custom-scrollbar">
        <div className="grid gap-px bg-white/[0.05] xl:grid-cols-2">
          <section className="bg-[#0a1424] p-4">
            <div className="mb-4 border-b border-white/[0.05] pb-3">
              <div className="text-xs font-black text-slate-200">
                运行与资金约束
              </div>
              <div className="mt-1 text-[10px] text-slate-600">
                控制单次金额、全局并发与账户总暴露
              </div>
            </div>
            <div className="space-y-4">
              <div className="space-y-1.5">
                <Label
                  htmlFor="t-trade-mode"
                  className="text-xs font-bold text-slate-400"
                >
                  运行模式
                </Label>
                <Select
                  value={form.mode}
                  onValueChange={value =>
                    setField('mode', value === 'live' ? 'live' : 'paper')
                  }
                >
                  <SelectTrigger
                    id="t-trade-mode"
                    className="h-9 rounded-sm border-white/10 bg-[#07111f] text-xs focus:ring-red-500/60"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="paper">
                      模拟观察（推荐先验证）
                    </SelectItem>
                    <SelectItem value="live">实盘执行</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-2 2xl:grid-cols-4">
                <NumericField
                  id="t-trade-target-amount"
                  label="目标单次金额"
                  suffix="元"
                  value={form.targetTradeAmount}
                  onChange={value => setField('targetTradeAmount', value)}
                />
                <NumericField
                  id="t-trade-max-amount"
                  label="单次金额硬上限"
                  suffix="元"
                  value={form.maxTradeAmount}
                  onChange={value => setField('maxTradeAmount', value)}
                />
                <NumericField
                  id="t-trade-concurrency"
                  label="账户并发批次"
                  suffix="批"
                  value={form.maxConcurrentBatches}
                  onChange={value => setField('maxConcurrentBatches', value)}
                />
                <NumericField
                  id="t-trade-total-exposure"
                  label="账户总 T 暴露"
                  suffix="%"
                  value={form.maxTotalTExposurePct}
                  onChange={value => setField('maxTotalTExposurePct', value)}
                />
              </div>

              <div className="border-t border-white/[0.05] pt-4">
                <div className="mb-3 text-[10px] font-black uppercase tracking-[0.12em] text-slate-600">
                  动态退出
                </div>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-2 2xl:grid-cols-4">
                  <NumericField
                    id="t-trade-target"
                    label="收益武装线"
                    suffix="%"
                    value={form.targetProfitPct}
                    onChange={value => setField('targetProfitPct', value)}
                  />
                  <NumericField
                    id="t-trade-floor"
                    label="初始保护线"
                    suffix="%"
                    value={form.baseFloorPct}
                    onChange={value => setField('baseFloorPct', value)}
                  />
                  <NumericField
                    id="t-trade-max-gap"
                    label="最大回撤宽度"
                    suffix="%"
                    value={form.maxGapPct}
                    onChange={value => setField('maxGapPct', value)}
                  />
                  <NumericField
                    id="t-trade-initial-gap"
                    label="初始回撤宽度"
                    suffix="%"
                    value={form.initialGapPct}
                    onChange={value => setField('initialGapPct', value)}
                  />
                  <NumericField
                    id="t-trade-gap-slope"
                    label="放宽斜率"
                    value={form.trailingGapSlope}
                    onChange={value => setField('trailingGapSlope', value)}
                  />
                </div>
              </div>

              <div className="border border-white/[0.07] bg-[#07111f]/60 p-3">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <Label
                      htmlFor="t-trade-hard-stop-enabled"
                      className="text-xs font-bold text-slate-300"
                    >
                      硬止损保护
                    </Label>
                    <p className="mt-1 text-[10px] text-slate-600">
                      可选风险底线；关闭后不会因亏损比例自动卖出
                    </p>
                  </div>
                  <input
                    id="t-trade-hard-stop-enabled"
                    type="checkbox"
                    checked={form.hardStopEnabled}
                    onChange={event =>
                      setField('hardStopEnabled', event.target.checked)
                    }
                    className="h-4 w-4 cursor-pointer accent-red-500 focus-visible:ring-2 focus-visible:ring-red-500/60"
                  />
                </div>
                {form.hardStopEnabled && (
                  <div className="mt-3 max-w-48 border-t border-white/[0.05] pt-3">
                    <NumericField
                      id="t-trade-hard-stop"
                      label="硬止损线"
                      suffix="%"
                      value={form.hardStopPct}
                      onChange={value => setField('hardStopPct', value)}
                    />
                  </div>
                )}
              </div>

              <div className="border border-white/[0.07] bg-[#07111f]/60 p-3">
                <Label
                  htmlFor="t-trade-time-exit-mode"
                  className="text-xs font-bold text-slate-300"
                >
                  时间退出策略
                </Label>
                <p className="mt-1 text-[10px] text-slate-600">
                  默认无限期保护，仅在明确选择后按时间自动卖出
                </p>
                <Select
                  value={form.timeExitMode}
                  onValueChange={value =>
                    setField(
                      'timeExitMode',
                      value as SettingsForm['timeExitMode']
                    )
                  }
                >
                  <SelectTrigger
                    id="t-trade-time-exit-mode"
                    className="mt-3 h-9 rounded-sm border-white/10 bg-[#07111f] text-xs focus:ring-red-500/60"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={TTradeTimeExitMode.Unlimited}>
                      无限期保护
                    </SelectItem>
                    <SelectItem value={TTradeTimeExitMode.EndOfDay}>
                      当日收盘前退出
                    </SelectItem>
                    <SelectItem value={TTradeTimeExitMode.MaxHoldingDays}>
                      持有 N 个交易日退出
                    </SelectItem>
                  </SelectContent>
                </Select>

                {form.timeExitMode !== TTradeTimeExitMode.Unlimited && (
                  <div className="mt-3 grid grid-cols-2 gap-3 border-t border-white/[0.05] pt-3">
                    {form.timeExitMode ===
                      TTradeTimeExitMode.MaxHoldingDays && (
                      <NumericField
                        id="t-trade-max-holding-days"
                        label="最长持有"
                        suffix="交易日"
                        value={form.maxHoldingTradingDays}
                        onChange={value =>
                          setField('maxHoldingTradingDays', value)
                        }
                      />
                    )}
                    <div className="space-y-1.5">
                      <Label
                        htmlFor="t-trade-time-exit-time"
                        className="text-xs font-bold text-slate-400"
                      >
                        退出时刻
                      </Label>
                      <Input
                        id="t-trade-time-exit-time"
                        type="time"
                        value={form.timeExitTime}
                        onChange={event =>
                          setField('timeExitTime', event.target.value)
                        }
                        className="h-9 rounded-sm border-white/10 bg-[#07111f] font-mono text-xs focus-visible:ring-red-500/60"
                      />
                    </div>
                  </div>
                )}

                {!form.hardStopEnabled &&
                  form.timeExitMode === TTradeTimeExitMode.Unlimited && (
                    <div className="mt-3 flex items-start gap-2 border-t border-amber-400/10 pt-3 text-[10px] leading-4 text-amber-200/80">
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      未达到收益武装线的批次可能长期持有，仍可通过人工操作结束。
                    </div>
                  )}
              </div>
            </div>
          </section>

          <section className="bg-[#0a1424] p-4">
            <div className="mb-4 border-b border-white/[0.05] pb-3">
              <div className="text-xs font-black text-slate-200">
                Tick 信号与确认
              </div>
              <div className="mt-1 text-[10px] text-slate-600">
                定义回撤企稳机会、确认有效期与冷却时间
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-2 2xl:grid-cols-4">
              <NumericField
                id="t-trade-lookback"
                label="回看窗口"
                suffix="秒"
                value={form.signalLookbackSeconds}
                onChange={value => setField('signalLookbackSeconds', value)}
              />
              <NumericField
                id="t-trade-stable"
                label="企稳时长"
                suffix="秒"
                value={form.stabilizationSeconds}
                onChange={value => setField('stabilizationSeconds', value)}
              />
              <NumericField
                id="t-trade-pullback"
                label="最低回撤"
                suffix="%"
                value={form.pullbackThresholdPct}
                onChange={value => setField('pullbackThresholdPct', value)}
              />
              <NumericField
                id="t-trade-rebound"
                label="最低反弹"
                suffix="%"
                value={form.reboundThresholdPct}
                onChange={value => setField('reboundThresholdPct', value)}
              />
              <NumericField
                id="t-trade-spread"
                label="最大价差"
                suffix="Tick"
                value={form.maxSpreadTicks}
                onChange={value => setField('maxSpreadTicks', value)}
              />
              <NumericField
                id="t-trade-ttl"
                label="确认有效期"
                suffix="秒"
                value={form.approvalTtlSeconds}
                onChange={value => setField('approvalTtlSeconds', value)}
              />
              <NumericField
                id="t-trade-deviation"
                label="确认价偏离"
                suffix="%"
                value={form.maxPriceDeviationPct}
                onChange={value => setField('maxPriceDeviationPct', value)}
              />
              <NumericField
                id="t-trade-cooldown"
                label="冷却时间"
                suffix="秒"
                value={form.cooldownSeconds}
                onChange={value => setField('cooldownSeconds', value)}
              />
            </div>

            <div className="mt-5 border-t border-white/[0.05] pt-4">
              <Label
                htmlFor="t-trade-ignore-code"
                className="text-xs font-bold text-slate-300"
              >
                忽略股票代码
              </Label>
              <p className="mt-1 text-[10px] text-slate-600">
                忽略名单优先于动态持仓范围，可随时恢复监控
              </p>
              <div className="mt-3 flex gap-2">
                <Input
                  id="t-trade-ignore-code"
                  value={ignoreInput}
                  onChange={event => setIgnoreInput(event.target.value)}
                  onKeyDown={event => {
                    if (event.key === 'Enter') {
                      event.preventDefault();
                      handleAddIgnore();
                    }
                  }}
                  placeholder="例如 600000 或 600000.SH"
                  className="h-9 rounded-sm border-white/10 bg-[#07111f] font-mono text-xs focus-visible:ring-red-500/60"
                />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-9 rounded-sm border-white/10"
                  disabled={!ignoreInput.trim() || actionLoading}
                  onClick={handleAddIgnore}
                >
                  添加
                </Button>
              </div>
              <div className="mt-3 flex min-h-8 flex-wrap gap-1.5">
                {ignoredCodes.length === 0 ? (
                  <span className="text-[10px] text-slate-700">
                    当前未忽略任何股票
                  </span>
                ) : (
                  ignoredCodes.map(code => (
                    <button
                      key={code}
                      type="button"
                      disabled={actionLoading}
                      onClick={() => handleIgnore(code, false)}
                      className="inline-flex items-center gap-1 border border-white/10 bg-white/[0.04] px-2 py-1 font-mono text-[10px] text-slate-400 outline-none transition-colors hover:border-rose-400/30 hover:text-rose-200 focus-visible:ring-2 focus-visible:ring-red-500/60"
                      aria-label={`从忽略名单移除 ${code}`}
                    >
                      {code}
                      <X className="h-3 w-3" />
                    </button>
                  ))
                )}
              </div>
            </div>
          </section>
        </div>

        {form.mode === 'live' && (
          <label className="flex cursor-pointer items-start gap-2.5 border-t border-amber-400/15 bg-amber-400/[0.06] px-4 py-3 text-xs font-bold leading-5 text-amber-100">
            <input
              type="checkbox"
              checked={form.acknowledged}
              onChange={event => setField('acknowledged', event.target.checked)}
              className="mt-0.5 h-4 w-4 shrink-0 accent-amber-400"
            />
            我确认所有持仓形成 T
            批次后，当前已启用的退出规则可自动提交实盘卖单。
          </label>
        )}
      </div>

      <div className="flex shrink-0 items-center justify-between border-t border-white/[0.06] bg-[#091322] px-4 py-3">
        <div className="text-[10px] text-slate-600">
          保存后立即应用于当前账户的单一 T 策略运行
        </div>
        <Button
          type="button"
          className="h-9 rounded-sm bg-red-500 px-5 text-xs text-white hover:bg-red-400"
          disabled={
            !accountId ||
            actionLoading ||
            (form.mode === 'live' && !form.acknowledged)
          }
          onClick={() => persist(Boolean(monitor?.enabled))}
        >
          {saveResult.fetching ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Save className="mr-2 h-4 w-4" />
          )}
          保存全局设置
        </Button>
      </div>
    </div>
  );

  const content = (
    <div className="flex h-full min-h-0 flex-col">
      {toolbar}
      <div className="min-h-0 flex-1">
        {workspaceMode === 'REPLAY' ? (
          <TTradeReplayPanel accountId={accountId} form={form} />
        ) : activeMode === 'MONITOR' ? (
          monitorView
        ) : activeMode === 'SIGNALS' ? (
          signalsView
        ) : (
          settingsView
        )}
      </div>
    </div>
  );

  return (
    <StudioWorkbench
      activeMode={activeMode}
      className="h-full min-h-0"
      content={content}
      isPage
      modes={workspaceMode === 'REALTIME' ? tTradeModes : []}
      onModeChange={mode => setActiveMode(mode as TTradeStudioMode)}
      sidebar={workspaceMode === 'REPLAY' ? replaySidebar : sidebar}
      sidebarSizing={{
        defaultWidth: 312,
        maxWidth: 420,
        minWidth: 260,
        storageScope: 't-trade-studio',
      }}
      showSidebar
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                workspaceMode === 'REPLAY'
                  ? 'bg-cyan-400'
                  : monitor?.enabled
                    ? 'bg-emerald-400'
                    : 'bg-slate-600'
              )}
            />
            {workspaceMode === 'REPLAY'
              ? '历史回放测试模式'
              : monitor?.enabled
                ? '全局监控运行中'
                : '全局监控已停止'}
          </span>
          <span className="text-slate-700">|</span>
          <span className="font-mono">{accountId || '未选择账户'}</span>
          {workspaceMode === 'REALTIME' && (
            <>
              <span className="text-slate-700">|</span>
              <span className="inline-flex items-center gap-1.5">
                <Clock3 className="h-3 w-3" />
                最近同步 {formatTime(monitor?.lastReconciledAt)}
              </span>
            </>
          )}
        </>
      }
      statusBarRight={
        workspaceMode === 'REPLAY' ? (
          <>
            <span>BACKTEST Broker</span>
            <span className="text-slate-700">|</span>
            <span>最长 20 个交易日</span>
            <span className="text-slate-700">|</span>
            <span>实时监控互不影响</span>
          </>
        ) : (
          <>
            <span className="font-mono">
              运行 {monitor?.strategyRunId?.slice(0, 8) || '--'}
            </span>
            <span className="text-slate-700">|</span>
            <span>
              标的 v{monitor?.universeRevision ?? 0} · 配置 v
              {monitor?.configVersion ?? 0}
            </span>
            <span className="text-slate-700">|</span>
            <span>
              待确认 {monitor?.pendingSignalCount ?? 0} · 活跃{' '}
              {monitor?.activeBatchCount ?? 0}
            </span>
          </>
        )
      }
      theme={{
        icon: workspaceMode === 'REPLAY' ? FlaskConical : Radar,
        name: 'red',
        title: workspaceMode === 'REPLAY' ? '做T回放测试' : '做T助手',
      }}
    />
  );
}
