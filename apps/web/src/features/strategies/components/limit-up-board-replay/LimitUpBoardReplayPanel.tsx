import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Database,
  Loader2,
  Play,
  RefreshCw,
  Square,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/utils/cn';

import { useLimitUpBoardReplay } from '../../hooks/useLimitUpBoardReplay';

const ACTIVE_STATUSES = new Set(['PENDING', 'STARTING', 'RUNNING']);

function localDateTimeValue(date: Date) {
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function initialRange() {
  const now = new Date();
  const end = new Date(now);
  end.setHours(15, 0, 0, 0);
  if (end.getTime() > now.getTime()) end.setDate(end.getDate() - 1);
  const start = new Date(end);
  start.setDate(start.getDate() - 7);
  start.setHours(9, 30, 0, 0);
  return { start: localDateTimeValue(start), end: localDateTimeValue(end) };
}

function toIso(value: string) {
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) ? parsed.toISOString() : '';
}

function formatMoney(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return '--';
  return `¥${value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
}

function formatPct(value?: number | null, digits = 2) {
  if (value == null || !Number.isFinite(value)) return '--';
  return `${value.toFixed(digits)}%`;
}

function formatDateTime(value?: string | null) {
  if (!value) return '--';
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime())
    ? parsed.toLocaleString('zh-CN', { hour12: false })
    : value;
}

function statusLabel(status?: string) {
  return (
    {
      CANCELLED: '已取消',
      COMPLETED: '已完成',
      ERROR: '失败',
      FAILED: '失败',
      PENDING: '等待中',
      RUNNING: '执行中',
      STARTING: '准备数据',
    }[status || ''] ||
    status ||
    '未知'
  );
}

function statusTone(status?: string) {
  if (status === 'COMPLETED')
    return 'text-emerald-300 border-emerald-400/25 bg-emerald-400/10';
  if (status === 'FAILED' || status === 'ERROR')
    return 'text-rose-300 border-rose-400/25 bg-rose-400/10';
  if (status === 'CANCELLED')
    return 'text-slate-400 border-white/10 bg-white/[0.04]';
  return 'text-amber-200 border-amber-400/25 bg-amber-400/10';
}

function Metric({
  label,
  value,
  alert = false,
}: {
  label: string;
  value: string;
  alert?: boolean;
}) {
  return (
    <div className="min-w-[118px] border-r border-white/[0.06] px-3 py-2.5 last:border-r-0">
      <div className="text-ui-micro font-bold uppercase tracking-[0.12em] text-slate-600">
        {label}
      </div>
      <div
        className={cn(
          'mt-1 font-mono text-ui-body font-black text-slate-100',
          alert && 'text-rose-300'
        )}
      >
        {value}
      </div>
    </div>
  );
}

function EquityCurve({
  points,
}: {
  points: ReadonlyArray<{
    timestamp: string;
    equity: number;
    returnPct: number;
  }>;
}) {
  const geometry = useMemo(() => {
    if (points.length < 2) return null;
    const values = points.map(point => point.equity);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const spread = Math.max(max - min, Math.abs(max) * 0.001, 1);
    const path = points
      .map((point, index) => {
        const x = (index / (points.length - 1)) * 720;
        const y = 136 - ((point.equity - min) / spread) * 124;
        return `${index ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(' ');
    return { max, min, path };
  }, [points]);

  if (!geometry) {
    return (
      <div className="flex h-40 items-center justify-center text-ui-caption text-slate-600">
        场景完成后显示权益曲线
      </div>
    );
  }
  const positive =
    points.at(-1)?.returnPct != null && (points.at(-1)?.returnPct || 0) >= 0;
  return (
    <div className="relative h-40 px-2 py-3">
      <div className="absolute left-3 top-2 font-mono text-ui-micro text-slate-600">
        {formatMoney(geometry.max)}
      </div>
      <div className="absolute bottom-2 left-3 font-mono text-ui-micro text-slate-600">
        {formatMoney(geometry.min)}
      </div>
      <svg
        aria-label="场景权益曲线"
        className="h-full w-full"
        preserveAspectRatio="none"
        role="img"
        viewBox="0 0 720 148"
      >
        <line stroke="rgba(148,163,184,.12)" x1="0" x2="720" y1="74" y2="74" />
        <path
          d={geometry.path}
          fill="none"
          stroke={positive ? '#34d399' : '#fb7185'}
          strokeLinecap="round"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </div>
  );
}

export function LimitUpBoardReplayPanel({ accountId }: { accountId?: string }) {
  const { toast } = useToast();
  const defaults = useMemo(initialRange, []);
  const [startLocal, setStartLocal] = useState(defaults.start);
  const [endLocal, setEndLocal] = useState(defaults.end);
  const [initialCash, setInitialCash] = useState('');
  const [initialTotalAsset, setInitialTotalAsset] = useState('');
  const [scenarioId, setScenarioId] = useState<string>();
  const replay = useLimitUpBoardReplay(
    accountId,
    toIso(startLocal),
    toIso(endLocal),
    scenarioId
  );
  const selected = replay.selectedReplay;
  const scenarios = useMemo(
    () => selected?.scenarios ?? replay.preparation?.scenarios ?? [],
    [replay.preparation?.scenarios, selected?.scenarios]
  );
  const selectedScenario = selected?.scenarios.find(
    item => item.scenarioId === scenarioId
  );
  const active = Boolean(selected && ACTIVE_STATUSES.has(selected.status));

  useEffect(() => {
    if (scenarioId && scenarios.some(item => item.scenarioId === scenarioId))
      return;
    const base = scenarios.find(item => item.scenarioId === 'BASE');
    setScenarioId(base?.scenarioId || scenarios[0]?.scenarioId);
  }, [scenarioId, scenarios]);

  const run = async () => {
    try {
      const cash = initialCash.trim() ? Number(initialCash) : undefined;
      const totalAsset = initialTotalAsset.trim()
        ? Number(initialTotalAsset)
        : undefined;
      if (cash != null && (!Number.isFinite(cash) || cash < 0))
        throw new Error('初始现金必须是非负数字');
      if (
        totalAsset != null &&
        (!Number.isFinite(totalAsset) || totalAsset <= 0)
      )
        throw new Error('初始总资产必须是正数');
      if ((cash == null) !== (totalAsset == null))
        throw new Error('手工初始资产需要同时填写现金与总资产');
      if (cash != null && totalAsset != null && cash > totalAsset)
        throw new Error('初始现金不能超过初始总资产');
      const result = await replay.start(cash, totalAsset);
      toast({ title: '历史回放已提交', description: result.message });
    } catch (error) {
      toast({
        title: '无法启动历史回放',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      });
    }
  };

  const cancel = async () => {
    try {
      const result = await replay.cancel();
      toast({ title: '取消请求已提交', description: result.message });
    } catch (error) {
      toast({
        title: '无法取消历史回放',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      });
    }
  };

  const quality = selected?.dataQuality;
  const blockers = quality?.blockers ?? replay.preparation?.blockers ?? [];
  const warnings = quality?.warnings ?? replay.preparation?.warnings ?? [];
  const summary = selectedScenario?.summary;
  const funnel = selectedScenario?.funnel;

  return (
    <div
      className="grid h-full min-h-0 grid-cols-1 overflow-hidden border border-cyan-400/15 bg-[#0b1524] xl:grid-cols-[236px_minmax(0,1fr)]"
      data-testid="limit-up-board-replay-panel"
    >
      <aside className="min-h-0 overflow-y-auto border-b border-white/[0.07] bg-[#08111f] xl:border-b-0 xl:border-r custom-scrollbar">
        <div className="border-b border-white/[0.07] p-3">
          <div className="flex items-center gap-2 text-ui-label font-black text-slate-100">
            <BarChart3 className="h-3.5 w-3.5 text-cyan-300" />
            历史回放
          </div>
          <p className="mt-1.5 text-ui-micro leading-4 text-slate-600">
            账户级动态候选、资金竞争、T+1 与五档保守撮合。
          </p>
        </div>
        <div className="space-y-1 p-2">
          {replay.history.length ? (
            replay.history.map(job => (
              <button
                key={job.jobId}
                className={cn(
                  'w-full rounded-sm border px-2.5 py-2 text-left transition-colors',
                  replay.selectedJobId === job.jobId
                    ? 'border-cyan-400/30 bg-cyan-400/[0.08]'
                    : 'border-transparent hover:border-white/[0.07] hover:bg-white/[0.025]'
                )}
                onClick={() => replay.setSelectedJobId(job.jobId)}
                type="button"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-ui-caption font-bold text-slate-300">
                    {new Date(job.request.startTime).toLocaleDateString(
                      'zh-CN'
                    )}
                  </span>
                  <span
                    className={cn(
                      'text-ui-micro font-bold',
                      statusTone(job.status).split(' ')[0]
                    )}
                  >
                    {statusLabel(job.status)}
                  </span>
                </div>
                <div className="mt-1 truncate text-ui-micro text-slate-600">
                  {job.jobId.slice(0, 8)} · {job.progressPct.toFixed(0)}%
                </div>
              </button>
            ))
          ) : (
            <div className="px-2 py-ui-panel text-center text-ui-caption text-slate-600">
              尚无历史任务
            </div>
          )}
        </div>
      </aside>

      <div className="min-h-0 overflow-y-auto custom-scrollbar">
        <header className="sticky top-0 z-10 border-b border-white/[0.07] bg-[#0b1524]/95 p-3 backdrop-blur">
          <div className="grid gap-2 lg:grid-cols-2 xl:grid-cols-[1fr_1fr_150px_150px_auto]">
            <div>
              <Label className="text-ui-micro text-slate-500">开始时间</Label>
              <Input
                className="mt-1 h-control-compact border-white/10 bg-[#07101d] font-mono text-ui-caption"
                onChange={event => setStartLocal(event.target.value)}
                type="datetime-local"
                value={startLocal}
              />
            </div>
            <div>
              <Label className="text-ui-micro text-slate-500">结束时间</Label>
              <Input
                className="mt-1 h-control-compact border-white/10 bg-[#07101d] font-mono text-ui-caption"
                onChange={event => setEndLocal(event.target.value)}
                type="datetime-local"
                value={endLocal}
              />
            </div>
            <div>
              <Label className="text-ui-micro text-slate-500">
                初始现金（可选）
              </Label>
              <Input
                className="mt-1 h-control-compact border-white/10 bg-[#07101d] font-mono text-ui-caption"
                min="0"
                onChange={event => setInitialCash(event.target.value)}
                placeholder="使用账户快照"
                type="number"
                value={initialCash}
              />
            </div>
            <div>
              <Label className="text-ui-micro text-slate-500">
                初始总资产（可选）
              </Label>
              <Input
                className="mt-1 h-control-compact border-white/10 bg-[#07101d] font-mono text-ui-caption"
                min="0"
                onChange={event => setInitialTotalAsset(event.target.value)}
                placeholder="无快照时必填"
                type="number"
                value={initialTotalAsset}
              />
            </div>
            <div className="flex items-end gap-1.5">
              <Button
                className="h-control-compact bg-cyan-500 px-3 text-ui-caption font-black text-slate-950 hover:bg-cyan-400"
                disabled={
                  !accountId ||
                  !replay.preparation?.ready ||
                  replay.starting ||
                  Boolean(replay.activeReplay)
                }
                onClick={run}
                size="sm"
              >
                {replay.starting ? (
                  <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                ) : (
                  <Play className="mr-1.5 h-3 w-3" />
                )}
                启动
              </Button>
              {active ? (
                <Button
                  className="h-control-compact border-rose-400/25 px-2 text-ui-caption text-rose-200"
                  disabled={replay.cancelling}
                  onClick={cancel}
                  size="sm"
                  variant="outline"
                >
                  <Square className="mr-1 h-3 w-3" />
                  取消
                </Button>
              ) : null}
              <Button
                aria-label="刷新历史回放"
                className="h-control-compact w-8 text-slate-500"
                onClick={replay.refresh}
                size="icon"
                variant="ghost"
              >
                <RefreshCw
                  className={cn(
                    'h-3.5 w-3.5',
                    replay.fetching && 'animate-spin'
                  )}
                />
              </Button>
            </div>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-ui-micro text-slate-500">
            <span
              className={cn(
                'inline-flex items-center gap-1',
                replay.wsStatus === 'connected'
                  ? 'text-emerald-300'
                  : 'text-amber-300'
              )}
            >
              <span className="h-1.5 w-1.5 rounded-full bg-current" />
              {replay.wsStatus === 'connected' ? '实时进度' : '轮询恢复'}
            </span>
            <span>固定 STANDARD_V1 四情景</span>
            <span>·</span>
            <span>最多 20 个交易日</span>
            <span>·</span>
            <span>窗口末持仓不强平</span>
          </div>
        </header>

        {!selected ? (
          <section className="m-3 rounded-sm border border-white/[0.07] bg-white/[0.02] p-ui-section">
            <div className="flex items-center gap-2 text-ui-label font-black text-slate-200">
              <Database className="h-4 w-4 text-cyan-300" />
              创建第一份账户级历史回放
            </div>
            <p className="mt-2 max-w-3xl text-ui-caption leading-5 text-slate-500">
              启动后先物化不可变候选帧与原始五档
              Tick。任何时点污染、交易日缺失或盘口字段不完整都会阻止执行；通过后才运行四档确认延迟与参与率情景。
            </p>
            <div
              className={cn(
                'mt-4 flex items-start gap-2 rounded-sm border p-3 text-ui-caption',
                blockers.length
                  ? 'border-rose-400/20 bg-rose-400/[0.06] text-rose-200'
                  : 'border-emerald-400/20 bg-emerald-400/[0.05] text-emerald-200'
              )}
            >
              {replay.preparation?.ready ? (
                <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              ) : (
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              )}
              <div>
                {replay.preparation?.message ||
                  (replay.preparationError
                    ? replay.preparationError.message
                    : '正在检查助手配置与任务状态')}
              </div>
            </div>
          </section>
        ) : (
          <>
            <section className="border-b border-white/[0.07]">
              <div className="flex flex-wrap items-center justify-between gap-3 px-3 py-2.5">
                <div className="flex items-center gap-2">
                  <Badge
                    className={cn(
                      'border text-ui-micro',
                      statusTone(selected.status)
                    )}
                  >
                    {statusLabel(selected.status)}
                  </Badge>
                  <span className="font-mono text-ui-micro text-slate-600">
                    {selected.jobId}
                  </span>
                </div>
                <span className="text-ui-micro text-slate-500">
                  {formatDateTime(selected.request.startTime)} →{' '}
                  {formatDateTime(selected.request.endTime)}
                </span>
              </div>
              <div className="h-1 bg-white/[0.04]">
                <div
                  className="h-full bg-cyan-400 transition-[width]"
                  style={{
                    width: `${Math.max(0, Math.min(100, selected.progressPct))}%`,
                  }}
                />
              </div>
              {selected.errorMessage ? (
                <div className="flex items-start gap-2 border-t border-rose-400/15 bg-rose-400/[0.07] px-3 py-2 text-ui-caption text-rose-200">
                  <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                  {selected.errorMessage}
                </div>
              ) : null}
            </section>

            <section className="grid gap-px border-b border-white/[0.07] bg-white/[0.06] sm:grid-cols-2 xl:grid-cols-4">
              {selected.scenarios.map(item => (
                <button
                  key={item.scenarioId}
                  className={cn(
                    'bg-[#0b1524] p-3 text-left transition-colors hover:bg-white/[0.025]',
                    item.scenarioId === scenarioId &&
                      'bg-cyan-400/[0.07] ring-1 ring-inset ring-cyan-400/30'
                  )}
                  onClick={() => setScenarioId(item.scenarioId)}
                  type="button"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-ui-caption font-black text-slate-200">
                      {item.label}
                    </span>
                    {item.theoreticalUpperBound ? (
                      <Badge className="border-amber-400/25 bg-amber-400/10 text-ui-micro text-amber-200">
                        理论上界
                      </Badge>
                    ) : (
                      <span className="text-ui-micro text-slate-600">
                        {statusLabel(item.status)}
                      </span>
                    )}
                  </div>
                  <div className="mt-1.5 font-mono text-ui-micro text-slate-500">
                    确认 {item.confirmationDelayMs / 1_000}s · 成交量{' '}
                    {formatPct(item.participationCapPct * 100, 0)}
                  </div>
                  <div
                    className={cn(
                      'mt-2 font-mono text-ui-heading font-black',
                      (item.summary?.totalReturnPct || 0) >= 0
                        ? 'text-emerald-300'
                        : 'text-rose-300'
                    )}
                  >
                    {item.summary
                      ? formatPct(item.summary.totalReturnPct)
                      : `${item.progressPct.toFixed(0)}%`}
                  </div>
                </button>
              ))}
            </section>

            <section className="border-b border-white/[0.07]">
              <div className="grid grid-flow-col auto-cols-[minmax(118px,1fr)] overflow-x-auto custom-scrollbar">
                <Metric
                  label="期末权益"
                  value={formatMoney(summary?.finalEquity)}
                />
                <Metric
                  label="总收益"
                  value={formatPct(summary?.totalReturnPct)}
                  alert={(summary?.totalReturnPct || 0) < 0}
                />
                <Metric
                  label="最大回撤"
                  value={formatPct(summary?.maxDrawdownPct)}
                  alert={(summary?.maxDrawdownPct || 0) > 10}
                />
                <Metric
                  label="CVaR 95%"
                  value={formatPct(summary?.cvar95LossPct)}
                  alert={(summary?.cvar95LossPct || 0) > 0}
                />
                <Metric
                  label="成交率"
                  value={formatPct(summary?.fillRatePct)}
                />
                <Metric
                  label="窗口末持仓"
                  value={summary ? String(summary.openPositionCount) : '--'}
                  alert={(summary?.unsellablePositionCount || 0) > 0}
                />
              </div>
            </section>

            <section className="grid gap-px border-b border-white/[0.07] bg-white/[0.06] 2xl:grid-cols-[minmax(0,1.5fr)_minmax(360px,1fr)]">
              <div className="bg-[#0b1524]">
                <div className="border-b border-white/[0.06] px-3 py-2 text-ui-caption font-black text-slate-300">
                  权益曲线 · {selectedScenario?.label || '--'}
                </div>
                <EquityCurve points={replay.curve?.items ?? []} />
              </div>
              <div className="bg-[#0b1524]">
                <div className="border-b border-white/[0.06] px-3 py-2 text-ui-caption font-black text-slate-300">
                  候选到成交漏斗
                </div>
                <div className="grid grid-cols-3 gap-px bg-white/[0.05] sm:grid-cols-5 2xl:grid-cols-3">
                  {[
                    ['候选帧', funnel?.candidateFrames],
                    ['候选观察', funnel?.candidateObservations],
                    ['合格观察', funnel?.qualifiedObservations],
                    ['入场意图', funnel?.entryIntents],
                    ['确认到期', funnel?.approvalDue],
                    ['确认拒绝', funnel?.approvalRejected],
                    ['订单', funnel?.orders],
                    ['成交订单', funnel?.filledOrders],
                    ['完成退出', funnel?.completedExits],
                  ].map(([label, value]) => (
                    <div className="bg-[#0b1524] p-2.5" key={String(label)}>
                      <div className="text-ui-micro text-slate-600">
                        {label}
                      </div>
                      <div className="mt-1 font-mono text-ui-label font-black text-slate-200">
                        {value == null
                          ? '--'
                          : Number(value).toLocaleString('zh-CN')}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </section>

            <section className="grid gap-px border-b border-white/[0.07] bg-white/[0.06] xl:grid-cols-2">
              <div className="bg-[#0b1524] p-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-ui-caption font-black text-slate-300">
                    数据质量
                  </h3>
                  <Badge
                    className={cn(
                      'border text-ui-micro',
                      quality?.executable
                        ? 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300'
                        : 'border-rose-400/20 bg-rose-400/10 text-rose-300'
                    )}
                  >
                    {quality?.status || '准备中'}
                  </Badge>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2 text-ui-micro text-slate-500 sm:grid-cols-4">
                  <span>
                    候选帧{' '}
                    <strong className="font-mono text-slate-200">
                      {quality?.coverage.frameCount ?? '--'}
                    </strong>
                  </span>
                  <span>
                    Tick{' '}
                    <strong className="font-mono text-slate-200">
                      {quality?.rawTickCount ?? '--'}
                    </strong>
                  </span>
                  <span>
                    新鲜覆盖{' '}
                    <strong className="font-mono text-slate-200">
                      {quality ? formatPct(quality.freshCoverage) : '--'}
                    </strong>
                  </span>
                  <span>
                    缺五档{' '}
                    <strong className="font-mono text-slate-200">
                      {quality?.fiveLevelMissing ?? '--'}
                    </strong>
                  </span>
                </div>
                {blockers.length ? (
                  <div className="mt-3 space-y-1 text-ui-micro text-rose-300">
                    {blockers.map(item => (
                      <div key={item}>阻断 · {item}</div>
                    ))}
                  </div>
                ) : null}
                {warnings.length ? (
                  <div className="mt-3 space-y-1 text-ui-micro text-amber-200">
                    {warnings.slice(0, 5).map(item => (
                      <div key={item}>提示 · {item}</div>
                    ))}
                  </div>
                ) : null}
              </div>
              <div className="bg-[#0b1524] p-3">
                <h3 className="text-ui-caption font-black text-slate-300">
                  拒绝与约束
                </h3>
                <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-ui-micro">
                  {selectedScenario?.rejectionReasons.length ? (
                    selectedScenario.rejectionReasons.slice(0, 8).map(item => (
                      <div
                        className="flex justify-between gap-2 border-b border-white/[0.04] py-1"
                        key={item.reason}
                      >
                        <span className="truncate text-slate-500">
                          {item.reason}
                        </span>
                        <strong className="font-mono text-slate-200">
                          {item.count}
                        </strong>
                      </div>
                    ))
                  ) : (
                    <span className="col-span-2 text-slate-600">
                      暂无拒绝统计
                    </span>
                  )}
                </div>
              </div>
            </section>

            <section className="grid gap-px bg-white/[0.06] 2xl:grid-cols-2">
              <div className="min-w-0 bg-[#0b1524]">
                <div className="border-b border-white/[0.06] px-3 py-2 text-ui-caption font-black text-slate-300">
                  窗口末持仓（不伪造平仓）
                </div>
                <div className="overflow-x-auto custom-scrollbar">
                  <table className="w-full min-w-[560px] text-left text-ui-micro">
                    <thead className="text-slate-600">
                      <tr>
                        <th className="px-3 py-2">代码</th>
                        <th>数量/可卖</th>
                        <th>成本</th>
                        <th>末价</th>
                        <th>市值</th>
                        <th>状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedScenario?.openPositions.length ? (
                        selectedScenario.openPositions.map(item => (
                          <tr
                            className="border-t border-white/[0.04]"
                            key={item.instrumentCode}
                          >
                            <td className="px-3 py-2 font-mono font-bold text-slate-200">
                              {item.instrumentCode}
                            </td>
                            <td className="font-mono text-slate-400">
                              {item.volume}/{item.availableVolume}
                            </td>
                            <td className="font-mono text-slate-400">
                              {item.averagePrice.toFixed(2)}
                            </td>
                            <td className="font-mono text-slate-400">
                              {item.lastPrice.toFixed(2)}
                            </td>
                            <td className="font-mono text-slate-400">
                              {formatMoney(item.marketValue)}
                            </td>
                            <td className="text-amber-200">{item.status}</td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td
                            className="px-3 py-ui-panel text-center text-slate-600"
                            colSpan={6}
                          >
                            无窗口末持仓
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
              <div className="min-w-0 bg-[#0b1524]">
                <div className="border-b border-white/[0.06] px-3 py-2 text-ui-caption font-black text-slate-300">
                  最近成交
                </div>
                <div className="overflow-x-auto custom-scrollbar">
                  <table className="w-full min-w-[560px] text-left text-ui-micro">
                    <thead className="text-slate-600">
                      <tr>
                        <th className="px-3 py-2">时间</th>
                        <th>代码</th>
                        <th>方向</th>
                        <th>价格</th>
                        <th>数量</th>
                        <th>费用</th>
                      </tr>
                    </thead>
                    <tbody>
                      {replay.trades?.items.length ? (
                        replay.trades.items.map(item => (
                          <tr
                            className="border-t border-white/[0.04]"
                            key={item.tradeId}
                          >
                            <td className="px-3 py-2 font-mono text-slate-500">
                              {formatDateTime(item.tradeTime)}
                            </td>
                            <td className="font-mono font-bold text-slate-200">
                              {item.instrumentCode}
                            </td>
                            <td
                              className={
                                item.side === 'BUY'
                                  ? 'text-rose-300'
                                  : 'text-emerald-300'
                              }
                            >
                              {item.side}
                            </td>
                            <td className="font-mono text-slate-400">
                              {item.price.toFixed(2)}
                            </td>
                            <td className="font-mono text-slate-400">
                              {item.volume}
                            </td>
                            <td className="font-mono text-slate-400">
                              {item.fees.toFixed(2)}
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td
                            className="px-3 py-ui-panel text-center text-slate-600"
                            colSpan={6}
                          >
                            暂无成交
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
