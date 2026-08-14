import {
  AlertTriangle,
  Check,
  Clock3,
  RefreshCw,
  Settings2,
  ShieldCheck,
  ShieldX,
  Target,
  WalletCards,
  X,
} from 'lucide-react';
import { type ReactNode, useEffect, useMemo, useState } from 'react';
import { useLocation } from 'wouter';

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
import { Switch } from '@/components/ui/switch';
import { useCurrentAccount } from '@/features/dashboard/hooks/useDashboard';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/utils/cn';

import { LimitUpRadarPanel } from '../components/LimitUpRadarPanel';
import { useLimitUpBoardAssistant } from '../hooks/useLimitUpBoardAssistant';
import { useLimitUpRadar } from '../hooks/useLimitUpRadar';

function formatTime(value?: string | null) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

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
  };
  onApprove: () => void;
  onReject: () => void;
}) {
  const countdown = useCountdown(intent.approvalExpiresAt);
  return (
    <article className="overflow-hidden rounded-lg border border-amber-400/30 bg-amber-400/[0.07]">
      <div className="flex items-center justify-between gap-3 px-3 py-2.5">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs font-black text-slate-100">
              {intent.instrumentCode}
            </span>
            <Badge className="border-amber-400/25 bg-amber-400/10 text-[9px] text-amber-200 hover:bg-amber-400/10">
              待确认
            </Badge>
          </div>
          <div className="mt-1 text-[10px] text-slate-500">
            {formatMoney(intent.targetAmount)} · 涨停价{' '}
            {intent.limitUpPrice?.toFixed(2) ?? '--'}
          </div>
        </div>
        <div
          className={cn(
            'flex items-center gap-1 font-mono text-sm font-black',
            countdown.expired ? 'text-rose-300' : 'text-amber-200'
          )}
        >
          <Clock3 className="h-3.5 w-3.5" />
          {countdown.label}
        </div>
      </div>
      <div className="h-0.5 bg-white/[0.05]">
        <div
          className="h-full bg-amber-400 transition-[width] duration-200"
          style={{ width: `${countdown.progress}%` }}
        />
      </div>
      <div className="grid grid-cols-3 gap-px bg-white/[0.06] text-[9px]">
        <div className="bg-[#0d1626] px-3 py-2 text-slate-500">
          信号价
          <strong className="mt-0.5 block font-mono text-[11px] text-slate-200">
            {intent.signalPrice?.toFixed(2) ?? '--'}
          </strong>
        </div>
        <div className="bg-[#0d1626] px-3 py-2 text-slate-500">
          距涨停
          <strong className="mt-0.5 block font-mono text-[11px] text-slate-200">
            {intent.distanceToLimitTicks?.toFixed(0) ?? '--'} 档
          </strong>
        </div>
        <div className="bg-[#0d1626] px-3 py-2 text-slate-500">
          价格类型
          <strong className="mt-0.5 block text-[11px] text-slate-200">
            涨停限价
          </strong>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 p-2.5">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy || countdown.expired}
          onClick={onReject}
          className="h-8 border-white/10 bg-white/[0.025] text-[11px] text-slate-300 hover:bg-white/[0.06]"
        >
          <X className="h-3.5 w-3.5" />
          忽略
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={busy || countdown.expired || !canApprove}
          onClick={onApprove}
          className="h-8 bg-red-500 text-[11px] font-black text-white hover:bg-red-400"
          title={!canApprove ? '账户执行门禁尚未通过' : '确认后立即重新校验行情与风控'}
        >
          {busy ? (
            <RefreshCw className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Check className="h-3.5 w-3.5" />
          )}
          确认买入
        </Button>
      </div>
    </article>
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
  const rules = [
    plan.ruleTypes.includes('LIMIT_UP_BREAK') ? '破板 1 档全卖' : null,
    plan.ruleTypes.includes('TRAILING_PRICE_DRAWDOWN') ? '+2% 后回撤 3% 减半' : null,
    plan.ruleTypes.includes('MAX_HOLDING_DAYS') ? '第 2 日 14:50 清仓' : null,
  ].filter(Boolean);
  return (
    <article className="rounded-lg border border-emerald-400/20 bg-emerald-400/[0.045] p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs font-black text-slate-100">
              {plan.instrumentCode}
            </span>
            <Badge className="border-emerald-400/20 bg-emerald-400/10 text-[9px] text-emerald-200 hover:bg-emerald-400/10">
              {plan.pendingOrderId ? '退出委托中' : '自动托管'}
            </Badge>
          </div>
          <div className="mt-1 text-[10px] text-slate-500">
            剩余 {plan.remainingVolume.toLocaleString('zh-CN')} 股 · 成本{' '}
            {plan.entryAvgPrice.toFixed(2)}
          </div>
        </div>
        <div className="text-right">
          <div
            className={cn(
              'font-mono text-sm font-black',
              plan.lastNetProfitPct >= 0 ? 'text-red-300' : 'text-emerald-300'
            )}
          >
            {plan.lastNetProfitPct >= 0 ? '+' : ''}
            {plan.lastNetProfitPct.toFixed(2)}%
          </div>
          <div className="text-[9px] text-slate-600">现价 {plan.lastPrice.toFixed(2)}</div>
        </div>
      </div>
      <div className="mt-2 flex items-center justify-between rounded-md border border-white/[0.05] bg-black/10 px-2.5 py-2 text-[10px]">
        <span className="text-slate-500">T+1 状态</span>
        <span className={waitingT1 ? 'text-amber-200' : 'text-emerald-200'}>
          {waitingT1 ? '今日买入，等待可卖' : '已进入可卖日'}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {rules.map(rule => (
          <span
            key={rule}
            className="rounded border border-white/[0.07] bg-white/[0.025] px-1.5 py-1 text-[9px] text-slate-400"
          >
            {rule}
          </span>
        ))}
      </div>
      {!plan.autoExitAuthorized ? (
        <div className="mt-2 flex items-center gap-1.5 text-[9px] text-rose-300">
          <ShieldX className="h-3 w-3" /> 自动卖出授权未生效
        </div>
      ) : null}
    </article>
  );
}

export default function LimitUpBoardPage() {
  const [, setLocation] = useLocation();
  const { toast } = useToast();
  const accountResult = useCurrentAccount();
  const account = accountResult.data?.currentAccount;
  const accountId = account?.id;
  const radar = useLimitUpRadar(true);
  const assistant = useLimitUpBoardAssistant(accountId);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [targetAmount, setTargetAmount] = useState(10_000);
  const [positionCap, setPositionCap] = useState(5);
  const [scoreThreshold, setScoreThreshold] = useState(70);
  const [mode, setMode] = useState('paper');
  const [autoExitAcknowledged, setAutoExitAcknowledged] = useState(false);

  useEffect(() => {
    if (!settingsOpen) return;
    setTargetAmount(assistant.currentSettings.targetEntryAmount ?? 10_000);
    setPositionCap(
      (assistant.currentSettings.maxSinglePositionPct ?? 0.05) * 100
    );
    setScoreThreshold(assistant.currentSettings.autoSignalMinScore ?? 70);
    setMode(assistant.currentSettings.mode ?? 'paper');
    setAutoExitAcknowledged(
      assistant.currentSettings.autoExitAcknowledged ?? false
    );
  }, [assistant.currentSettings, settingsOpen]);

  const armedCodes = useMemo(
    () =>
      new Set(
        assistant.assistant?.armedCandidates.map(item => item.instrumentCode) ?? []
      ),
    [assistant.assistant?.armedCandidates]
  );
  const pendingCodes = useMemo(
    () => new Set(assistant.pendingIntents.map(item => item.instrumentCode)),
    [assistant.pendingIntents]
  );
  const exitPlanCodes = useMemo(
    () => new Set(assistant.exitPlans.map(item => item.instrumentCode)),
    [assistant.exitPlans]
  );
  const expiredWarnings = radar.summary.staleCount;
  const systemWarnings = useMemo(
    () =>
      radar.warnings.filter(
        warning =>
          !/候选.*过期|行情已过期|雷达数据提示|仅供观察/.test(warning)
      ),
    [radar.warnings]
  );
  const enabled = Boolean(assistant.assistant?.enabled);

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
        autoSignalMinScore: scoreThreshold,
        maxSinglePositionPct: positionCap / 100,
        mode,
        targetEntryAmount: targetAmount,
      });
      setSettingsOpen(false);
      toast({ title: '设置已保存', description: payload.message });
    });

  return (
    <div
      className="h-full overflow-y-auto bg-[#07101d] p-3 text-slate-100 custom-scrollbar"
      data-testid="limit-up-board-page"
    >
      <div className="mx-auto max-w-[1540px] space-y-3 pb-8">
        <header className="rounded-lg border border-white/[0.08] bg-[#0d1626]/95">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5">
            <div className="flex min-w-0 items-center gap-2">
              <Target className="h-4 w-4 shrink-0 text-red-400" />
              <div className="min-w-0">
                <div className="truncate text-xs font-black text-slate-100">
                  {account?.accountName || '未选择账户'}
                </div>
                <div className="truncate font-mono text-[9px] text-slate-600">
                  {accountId || '--'}
                </div>
              </div>
            </div>
            <span className="h-6 w-px bg-white/[0.08]" />
            <Badge
              className={cn(
                'border text-[9px] font-black hover:bg-transparent',
                assistant.currentSettings.mode === 'live'
                  ? 'border-rose-400/30 bg-rose-400/10 text-rose-200'
                  : 'border-cyan-400/25 bg-cyan-400/10 text-cyan-200'
              )}
            >
              {assistant.currentSettings.mode === 'live' ? '实盘' : '模拟'}
            </Badge>
            <label className="flex items-center gap-2 text-[10px] text-slate-400">
              <Switch
                checked={enabled}
                disabled={!accountId || busyAction === 'toggle'}
                onCheckedChange={checked =>
                  act('toggle', () => assistant.save({ enabled: checked }))
                }
                aria-label="启用打板助手"
                className="scale-75 data-[state=checked]:bg-emerald-500"
              />
              助手{enabled ? '运行中' : '已关闭'}
            </label>
            <StatusDot
              ok={radar.isScannerRunning}
              label={radar.isScannerRunning ? '雷达在线' : '雷达离线'}
            />
            <StatusDot
              ok={['ONLINE', 'READY'].includes(
                (assistant.assistant?.engineStatus || '').toUpperCase()
              )}
              label={`Engine ${assistant.assistant?.engineStatus || '未知'}`}
            />
            <span className="ml-auto text-[9px] text-slate-600">
              更新 {formatTime(radar.updatedAt)}
            </span>
            {expiredWarnings > 0 ? (
              <Badge className="border-amber-400/25 bg-amber-400/10 text-[9px] text-amber-200 hover:bg-amber-400/10">
                过期 {expiredWarnings}
              </Badge>
            ) : null}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-[10px] text-slate-400 hover:bg-white/[0.05] hover:text-slate-100"
              onClick={() =>
                act('refresh', async () => {
                  radar.refresh({ requestPolicy: 'network-only' });
                  await assistant.reconcile();
                })
              }
              disabled={!accountId || busyAction === 'refresh'}
            >
              <RefreshCw
                className={cn(
                  'h-3.5 w-3.5',
                  busyAction === 'refresh' && 'animate-spin'
                )}
              />
              同步
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 border-white/10 bg-white/[0.025] px-2 text-[10px] text-slate-300 hover:bg-white/[0.06]"
              onClick={() => setSettingsOpen(true)}
            >
              <Settings2 className="h-3.5 w-3.5" />
              设置
            </Button>
          </div>
          <div className="grid grid-cols-5 gap-px border-t border-white/[0.06] bg-white/[0.06]">
            {[
              ['市场候选', radar.summary.candidateCount],
              ['临板触板', radar.summary.nearLimitCount],
              ['监控标的', assistant.assistant?.monitoredCount ?? 0],
              ['待确认', assistant.pendingIntents.length],
              ['卖出托管', assistant.exitPlans.length],
            ].map(([label, value]) => (
              <div key={label} className="bg-[#0b1423] px-3 py-2">
                <span className="text-[9px] text-slate-600">{label}</span>
                <strong className="ml-2 font-mono text-sm text-slate-200">
                  {value}
                </strong>
              </div>
            ))}
          </div>
        </header>

        {assistant.assistant?.lastError ? (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-lg border border-rose-400/25 bg-rose-500/[0.08] px-3 py-2.5 text-[10px] text-rose-100"
          >
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <strong>助手受保护</strong>
            <span className="text-rose-100/75">
              {assistant.assistant.lastError}
            </span>
          </div>
        ) : null}

        <main className="grid min-w-0 gap-3 min-[1100px]:grid-cols-[minmax(0,1fr)_320px]">
          <LimitUpRadarPanel
            armedCodes={armedCodes}
            assistantEnabled={enabled}
            autoScore={assistant.assistant?.autoSignalMinScore ?? 70}
            busyCode={busyAction?.startsWith('candidate:') ? busyAction.slice(10) : null}
            candidates={radar.candidates}
            errorMessage={radar.error?.message}
            exitPlanCodes={exitPlanCodes}
            fetching={radar.fetching}
            industries={radar.industries}
            industry={radar.industry}
            isScannerRunning={radar.isScannerRunning}
            onArm={code => act(`candidate:${code}`, () => assistant.arm(code))}
            onDisarm={code =>
              act(`candidate:${code}`, () => assistant.disarm(code))
            }
            onIndustryChange={radar.setIndustry}
            onOpenStock={code =>
              setLocation(`/stock/${encodeURIComponent(code)}`)
            }
            onSearchChange={radar.setSearch}
            onStageChange={radar.setStage}
            pendingCodes={pendingCodes}
            search={radar.search}
            stage={radar.stage}
            summary={radar.summary}
            systemWarnings={systemWarnings}
          />

          <aside className="min-w-0 space-y-3" aria-label="打板执行操作栏">
            <section className="rounded-lg border border-amber-400/15 bg-[#0d1626]/90">
              <div className="flex items-center justify-between border-b border-white/[0.07] px-3 py-2.5">
                <h2 className="flex items-center gap-2 text-xs font-black">
                  <Clock3 className="h-3.5 w-3.5 text-amber-300" />
                  待确认信号
                </h2>
                <Badge className="border-amber-400/20 bg-amber-400/10 text-[9px] text-amber-200 hover:bg-amber-400/10">
                  {assistant.pendingIntents.length}
                </Badge>
              </div>
              <div className="max-h-[324px] space-y-2 overflow-y-auto p-2.5 custom-scrollbar">
                {assistant.pendingIntents.length ? (
                  assistant.pendingIntents.map(intent => (
                    <PendingSignalCard
                      key={intent.id}
                      intent={intent}
                      busy={busyAction === `intent:${intent.id}`}
                      canApprove={Boolean(assistant.assistant?.canApprove)}
                      onApprove={() =>
                        act(`intent:${intent.id}`, () => assistant.approve(intent.id))
                      }
                      onReject={() =>
                        act(`intent:${intent.id}`, () => assistant.reject(intent.id))
                      }
                    />
                  ))
                ) : (
                  <EmptyRail
                    icon={ShieldCheck}
                    title="暂无待确认信号"
                    description="助手只在候选进入临板触发区时生成一次 15 秒确认卡。"
                  />
                )}
              </div>
            </section>

            <section className="rounded-lg border border-emerald-400/15 bg-[#0d1626]/90">
              <div className="flex items-center justify-between border-b border-white/[0.07] px-3 py-2.5">
                <h2 className="flex items-center gap-2 text-xs font-black">
                  <WalletCards className="h-3.5 w-3.5 text-emerald-300" />
                  打板退出计划
                </h2>
                <Badge className="border-emerald-400/20 bg-emerald-400/10 text-[9px] text-emerald-200 hover:bg-emerald-400/10">
                  {assistant.exitPlans.length}
                </Badge>
                <Button
                  className="h-6 px-2 text-[9px]"
                  onClick={() => setLocation('/liquidation')}
                  size="sm"
                  variant="ghost"
                >
                  卖出管理
                </Button>
              </div>
              <div className="max-h-[410px] space-y-2 overflow-y-auto p-2.5 custom-scrollbar">
                {assistant.exitPlans.length ? (
                  assistant.exitPlans.map(plan => (
                    <ExitPlanCard key={plan.id} plan={plan} />
                  ))
                ) : (
                  <EmptyRail
                    icon={WalletCards}
                    title="暂无托管仓位"
                    description="受托买入的真实成交回报到达后，退出计划会自动出现在这里。"
                  />
                )}
              </div>
            </section>
          </aside>
        </main>
      </div>

      <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent className="border-white/10 bg-[#0d1626] text-slate-100 sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>打板助手设置</DialogTitle>
            <DialogDescription className="text-slate-500">
              每个账户只有一个助手。买入始终人工确认，成交后由 Engine 托管退出。
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2 sm:grid-cols-2">
            <SettingField label="单笔目标金额（元）">
              <Input
                type="number"
                min={100}
                step={100}
                value={targetAmount}
                onChange={event => setTargetAmount(Number(event.target.value))}
                className="border-white/10 bg-[#08111f]"
              />
            </SettingField>
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
            <SettingField label="自动布防评分">
              <Input
                type="number"
                min={0}
                max={100}
                step={1}
                value={scoreThreshold}
                onChange={event => setScoreThreshold(Number(event.target.value))}
                className="border-white/10 bg-[#08111f]"
              />
            </SettingField>
            <SettingField label="运行环境">
              <select
                value={mode}
                onChange={event => setMode(event.target.value)}
                className="h-10 w-full rounded-md border border-white/10 bg-[#08111f] px-3 text-sm outline-none focus:ring-2 focus:ring-cyan-400/50"
              >
                <option value="paper">模拟盘</option>
                <option value="live">实盘</option>
              </select>
            </SettingField>
          </div>
          {mode === 'live' ? (
            <label className="flex items-start gap-2 rounded-lg border border-rose-400/20 bg-rose-500/[0.06] p-3 text-[11px] leading-5 text-slate-300">
              <Checkbox
                checked={autoExitAcknowledged}
                onCheckedChange={value =>
                  setAutoExitAcknowledged(value === true)
                }
                className="mt-0.5"
              />
              <span>
                我确认：买入仍需人工点击，但真实成交后 Engine 将按破板、回撤和最长持有规则自动卖出。
              </span>
            </label>
          ) : null}
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.025] p-3 text-[10px] leading-5 text-slate-500">
            固定约束：09:30–14:50、距涨停不超过 1 档、执行行情 ≤3 秒、确认/委托各 15 秒、价格偏离 ≤20bps。
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
                targetAmount <= 0 ||
                positionCap <= 0 ||
                positionCap > 30 ||
                scoreThreshold < 0 ||
                scoreThreshold > 100 ||
                (mode === 'live' && !autoExitAcknowledged)
              }
              className="bg-red-500 text-white hover:bg-red-400"
            >
              {busyAction === 'settings' ? '保存中' : '保存设置'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function StatusDot({ label, ok }: { label: string; ok: boolean }) {
  return (
    <span className="flex items-center gap-1.5 text-[10px] text-slate-500">
      <span className={cn('h-1.5 w-1.5 rounded-full', ok ? 'bg-emerald-400' : 'bg-rose-400')} />
      {label}
    </span>
  );
}

function EmptyRail({
  description,
  icon: Icon,
  title,
}: {
  description: string;
  icon: typeof ShieldCheck;
  title: string;
}) {
  return (
    <div className="rounded-lg border border-dashed border-white/10 px-4 py-7 text-center">
      <Icon className="mx-auto h-6 w-6 text-slate-700" />
      <div className="mt-2 text-[11px] font-black text-slate-300">{title}</div>
      <p className="mx-auto mt-1 max-w-[250px] text-[9px] leading-4 text-slate-600">
        {description}
      </p>
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
      <Label className="text-[10px] font-bold text-slate-400">{label}</Label>
      {children}
    </div>
  );
}
