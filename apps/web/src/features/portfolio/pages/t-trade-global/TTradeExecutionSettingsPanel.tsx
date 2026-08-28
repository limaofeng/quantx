import {
  AlertTriangle,
  ChevronDown,
  Clock3,
  Gauge,
  Layers3,
  PencilLine,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  WalletCards,
  Zap,
} from 'lucide-react';
import * as React from 'react';

import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { TTradeTimeExitMode } from '@/generated/gql/graphql';
import { cn } from '@/utils/cn';

import type { SettingsForm } from './types';

type SettingsFieldChange = <K extends keyof SettingsForm>(
  key: K,
  value: SettingsForm[K]
) => void;

type GuardrailId =
  'hard-stop' | 'high-profit' | 'limit-up' | 'rapid-reversal' | 'time-exit';

const numberFormatter = new Intl.NumberFormat('zh-CN', {
  maximumFractionDigits: 2,
});

function displayNumber(value: string) {
  if (value.trim() === '') return '--';
  const parsed = Number(value);
  return Number.isFinite(parsed) ? numberFormatter.format(parsed) : value;
}

function displayPercent(value: string) {
  return `${displayNumber(value)}%`;
}

function ExecutionNumericField({
  id,
  label,
  onChange,
  suffix,
  value,
}: {
  id: string;
  label: string;
  onChange: (value: string) => void;
  suffix?: string;
  value: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label
        htmlFor={id}
        className="text-ui-label font-semibold text-slate-300"
      >
        {label}
      </Label>
      <div className="relative">
        <Input
          id={id}
          inputMode="decimal"
          value={value}
          onChange={event => onChange(event.target.value)}
          className="h-control-default rounded-control border-white/10 bg-[#07111f] pr-12 font-mono text-ui-label tabular-nums focus-visible:ring-primary/70"
        />
        {suffix && (
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-ui-caption font-semibold text-slate-500">
            {suffix}
          </span>
        )}
      </div>
    </div>
  );
}

function SummaryMetric({
  description,
  expanded,
  icon: Icon,
  label,
  onEdit,
  value,
}: {
  description: string;
  expanded: boolean;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  onEdit: () => void;
  value: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-controls="t-trade-funds-editor"
      aria-expanded={expanded}
      className="group flex min-h-24 cursor-pointer items-start gap-3 rounded-control border border-white/[0.07] bg-[#0d192b] p-3 text-left outline-none transition-colors hover:border-primary/35 hover:bg-primary/[0.06] focus-visible:ring-2 focus-visible:ring-primary/70"
      onClick={onEdit}
    >
      <span className="flex h-control-default w-control-default shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Icon className="h-4 w-4" aria-hidden="true" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center justify-between gap-2 text-ui-label font-semibold text-slate-300">
          {label}
          <PencilLine
            className="h-3.5 w-3.5 shrink-0 text-slate-600 transition-colors group-hover:text-primary"
            aria-hidden="true"
          />
        </span>
        <span className="mt-1.5 block font-mono text-ui-heading font-semibold tabular-nums text-slate-50">
          {value}
        </span>
        <span className="mt-1 block text-ui-caption leading-4 text-slate-500">
          {description}
        </span>
      </span>
    </button>
  );
}

function ExitTrajectory({ form }: { form: SettingsForm }) {
  const description = `收益达到 ${displayPercent(form.targetProfitPct)} 后启动保护，初始保护线 ${displayPercent(form.baseFloorPct)}，回撤宽度从 ${displayPercent(form.initialGapPct)} 逐步放宽到 ${displayPercent(form.maxGapPct)}。`;

  return (
    <div
      className="rounded-control border border-white/[0.07] bg-[#081422] p-3"
      role="img"
      aria-label={description}
    >
      <div className="flex items-center justify-between gap-3 text-ui-caption text-slate-500">
        <span>账户收益</span>
        <span>随收益增长自动调整保护位</span>
      </div>
      <svg
        aria-hidden="true"
        className="mt-2 h-36 w-full"
        viewBox="0 0 560 144"
        preserveAspectRatio="none"
      >
        <line
          x1="28"
          y1="126"
          x2="540"
          y2="126"
          stroke="rgba(148, 163, 184, 0.28)"
          strokeWidth="1"
        />
        <line
          x1="28"
          y1="18"
          x2="28"
          y2="126"
          stroke="rgba(148, 163, 184, 0.28)"
          strokeWidth="1"
        />
        <path
          d="M 32 116 H 152 V 90 H 292 V 64 H 414 V 34 H 534"
          fill="none"
          stroke="#3B82F6"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="3"
        />
        <path
          d="M 32 124 H 152 V 110 H 292 V 96 H 414 V 76 H 534"
          fill="none"
          stroke="rgba(148, 163, 184, 0.72)"
          strokeDasharray="7 6"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2"
        />
        {[
          ['152', '90'],
          ['292', '64'],
          ['414', '34'],
        ].map(([cx, cy]) => (
          <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="4" fill="#3B82F6" />
        ))}
      </svg>
      <span className="sr-only">{description}</span>
    </div>
  );
}

function GuardrailShell({
  children,
  enabled,
  icon: Icon,
  id,
  onOpenChange,
  open,
  summary,
  title,
  tone = 'blue',
  toggle,
}: {
  children: React.ReactNode;
  enabled?: boolean;
  icon: React.ComponentType<{ className?: string }>;
  id: GuardrailId;
  onOpenChange: (id: GuardrailId) => void;
  open: boolean;
  summary: string;
  title: string;
  tone?: 'amber' | 'blue' | 'rose';
  toggle?: React.ReactNode;
}) {
  const iconTone = {
    amber: 'bg-amber-400/10 text-amber-300',
    blue: 'bg-primary/10 text-primary',
    rose: 'bg-rose-400/10 text-rose-300',
  }[tone];

  return (
    <div
      className={cn(
        'rounded-control border bg-[#0b1728] transition-colors',
        open ? 'border-primary/35' : 'border-white/[0.07]'
      )}
    >
      <div className="flex min-h-16 items-center gap-2 p-2.5">
        <button
          type="button"
          aria-controls={`t-trade-${id}-editor`}
          aria-expanded={open}
          className="flex min-w-0 flex-1 cursor-pointer items-center gap-2.5 rounded-sm p-1 text-left outline-none transition-colors hover:bg-white/[0.025] focus-visible:ring-2 focus-visible:ring-primary/70"
          onClick={() => onOpenChange(id)}
        >
          <span
            className={cn(
              'flex h-control-compact w-control-compact shrink-0 items-center justify-center rounded-full',
              iconTone
            )}
          >
            <Icon className="h-4 w-4" aria-hidden="true" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-2 text-ui-label font-semibold text-slate-200">
              {title}
              {enabled !== undefined && (
                <span
                  className={cn(
                    'rounded-sm border px-1.5 py-0.5 text-ui-micro font-semibold',
                    enabled
                      ? 'border-emerald-400/15 bg-emerald-400/[0.07] text-emerald-300'
                      : 'border-white/[0.07] bg-white/[0.025] text-slate-500'
                  )}
                >
                  {enabled ? '已启用' : '未启用'}
                </span>
              )}
            </span>
            <span className="mt-0.5 block truncate text-ui-caption text-slate-500">
              {summary}
            </span>
          </span>
          <ChevronDown
            className={cn(
              'h-4 w-4 shrink-0 text-slate-500 transition-transform duration-200 motion-reduce:transition-none',
              open && 'rotate-180 text-primary'
            )}
            aria-hidden="true"
          />
        </button>
        {toggle}
      </div>
      {open && (
        <div
          id={`t-trade-${id}-editor`}
          className="animate-in border-t border-white/[0.06] px-3 pb-3 pt-3 fade-in slide-in-from-top-1 duration-150 motion-reduce:animate-none"
        >
          {children}
        </div>
      )}
    </div>
  );
}

function EnabledSwitch({
  checked,
  label,
  onCheckedChange,
}: {
  checked: boolean;
  label: string;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <Switch
      aria-label={label}
      checked={checked}
      onCheckedChange={onCheckedChange}
      className="focus-visible:ring-primary/70 focus-visible:ring-offset-[#0b1728] data-[state=checked]:bg-primary data-[state=unchecked]:bg-slate-700"
    />
  );
}

function disabledEditorHint({ title }: { title: string }) {
  return (
    <p className="text-ui-caption leading-5 text-slate-500">
      先启用“{title}”，再设置对应阈值。
    </p>
  );
}

function timeExitSummary(form: SettingsForm) {
  if (form.timeExitMode === TTradeTimeExitMode.EndOfDay) {
    return `当日 ${form.timeExitTime} 前退出`;
  }
  if (form.timeExitMode === TTradeTimeExitMode.MaxHoldingDays) {
    return `最多持有 ${displayNumber(form.maxHoldingTradingDays)} 个交易日，${form.timeExitTime} 退出`;
  }
  return '无限期保护，不因时间自动退出';
}

export function TTradeExecutionSettingsPanel({
  form,
  onFieldChange,
}: {
  form: SettingsForm;
  onFieldChange: SettingsFieldChange;
}) {
  const [fundsOpen, setFundsOpen] = React.useState(false);
  const [trajectoryOpen, setTrajectoryOpen] = React.useState(false);
  const [openGuardrail, setOpenGuardrail] = React.useState<GuardrailId | null>(
    null
  );

  const toggleGuardrail = (id: GuardrailId) => {
    setOpenGuardrail(current => (current === id ? null : id));
  };

  return (
    <section
      aria-labelledby="t-trade-execution-settings-title"
      className="bg-[#0a1424] p-ui-section xl:col-span-2"
    >
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3 border-b border-white/[0.05] pb-3">
        <div>
          <h3
            id="t-trade-execution-settings-title"
            className="text-ui-label font-bold text-slate-100"
          >
            运行与资金约束
          </h3>
          <p className="mt-1 text-ui-caption text-slate-500">
            先看策略轮廓，再按需调整细节
          </p>
        </div>
        <div
          aria-label="运行模式"
          className="flex rounded-control border border-white/[0.07] bg-[#07111f] p-1"
          role="group"
        >
          <button
            type="button"
            aria-pressed={form.mode === 'paper'}
            className={cn(
              'flex h-control-compact cursor-pointer items-center gap-1.5 rounded-sm px-3 text-ui-caption font-semibold outline-none transition-colors focus-visible:ring-2 focus-visible:ring-primary/70',
              form.mode === 'paper'
                ? 'bg-primary/15 text-blue-200'
                : 'text-slate-500 hover:text-slate-300'
            )}
            onClick={() => onFieldChange('mode', 'paper')}
          >
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
            模拟观察
          </button>
          <button
            type="button"
            aria-pressed={form.mode === 'live'}
            className={cn(
              'flex h-control-compact cursor-pointer items-center gap-1.5 rounded-sm px-3 text-ui-caption font-semibold outline-none transition-colors focus-visible:ring-2 focus-visible:ring-primary/70',
              form.mode === 'live'
                ? 'bg-primary/15 text-blue-200'
                : 'text-slate-500 hover:text-slate-300'
            )}
            onClick={() => onFieldChange('mode', 'live')}
          >
            <ShieldAlert className="h-3.5 w-3.5" aria-hidden="true" />
            实盘执行
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 xl:grid-cols-4">
        <SummaryMetric
          description="策略计划使用的理想单次金额"
          expanded={fundsOpen}
          icon={WalletCards}
          label="目标单次金额"
          onEdit={() => setFundsOpen(true)}
          value={<>¥ {displayNumber(form.targetTradeAmount)}</>}
        />
        <SummaryMetric
          description="任何单次批次都不能突破"
          expanded={fundsOpen}
          icon={Gauge}
          label="单次金额上限"
          onEdit={() => setFundsOpen(true)}
          value={<>¥ {displayNumber(form.maxTradeAmount)}</>}
        />
        <SummaryMetric
          description="同一账户可同时运行的批次数"
          expanded={fundsOpen}
          icon={Layers3}
          label="账户并发"
          onEdit={() => setFundsOpen(true)}
          value={<>{displayNumber(form.maxConcurrentBatches)} 批</>}
        />
        <SummaryMetric
          description="账户做 T 风险暴露的总上限"
          expanded={fundsOpen}
          icon={TrendingUp}
          label="账户总 T 暴露"
          onEdit={() => setFundsOpen(true)}
          value={displayPercent(form.maxTotalTExposurePct)}
        />
      </div>

      {fundsOpen && (
        <div
          id="t-trade-funds-editor"
          className="mt-2 rounded-control border border-primary/20 bg-primary/[0.035] p-3"
        >
          <div className="mb-3 flex items-center justify-between gap-3">
            <div>
              <div className="text-ui-label font-semibold text-slate-200">
                调整资金边界
              </div>
              <p className="mt-0.5 text-ui-caption text-slate-500">
                目标值用于计划，硬上限与账户暴露负责兜底。
              </p>
            </div>
            <button
              type="button"
              className="h-control-compact cursor-pointer rounded-sm px-2 text-ui-caption font-semibold text-slate-400 outline-none transition-colors hover:bg-white/[0.04] hover:text-slate-200 focus-visible:ring-2 focus-visible:ring-primary/70"
              onClick={() => setFundsOpen(false)}
            >
              收起
            </button>
          </div>
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            <ExecutionNumericField
              id="t-trade-target-amount"
              label="目标单次金额"
              suffix="元"
              value={form.targetTradeAmount}
              onChange={value => onFieldChange('targetTradeAmount', value)}
            />
            <ExecutionNumericField
              id="t-trade-max-amount"
              label="单次金额硬上限"
              suffix="元"
              value={form.maxTradeAmount}
              onChange={value => onFieldChange('maxTradeAmount', value)}
            />
            <ExecutionNumericField
              id="t-trade-concurrency"
              label="账户并发批次"
              suffix="批"
              value={form.maxConcurrentBatches}
              onChange={value => onFieldChange('maxConcurrentBatches', value)}
            />
            <ExecutionNumericField
              id="t-trade-total-exposure"
              label="账户总 T 暴露"
              suffix="%"
              value={form.maxTotalTExposurePct}
              onChange={value => onFieldChange('maxTotalTExposurePct', value)}
            />
          </div>
        </div>
      )}

      <div className="mt-3 grid gap-3 xl:grid-cols-12">
        <div className="rounded-panel border border-white/[0.07] bg-[#0b1728] p-3 xl:col-span-7">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h4 className="flex items-center gap-2 text-ui-title font-semibold text-slate-100">
                <TrendingUp
                  className="h-4 w-4 text-primary"
                  aria-hidden="true"
                />
                收益保护轨迹
              </h4>
              <p className="mt-1 text-ui-caption text-slate-500">
                随着收益增长，自动提高保护位并限制利润回吐
              </p>
            </div>
            <button
              type="button"
              aria-controls="t-trade-trajectory-editor"
              aria-expanded={trajectoryOpen}
              className="flex h-control-compact cursor-pointer items-center gap-1 rounded-sm px-2 text-ui-caption font-semibold text-slate-400 outline-none transition-colors hover:bg-primary/[0.06] hover:text-blue-200 focus-visible:ring-2 focus-visible:ring-primary/70"
              onClick={() => setTrajectoryOpen(current => !current)}
            >
              {trajectoryOpen ? '收起' : '精确调整'}
              <ChevronDown
                className={cn(
                  'h-3.5 w-3.5 transition-transform duration-200 motion-reduce:transition-none',
                  trajectoryOpen && 'rotate-180'
                )}
                aria-hidden="true"
              />
            </button>
          </div>

          <div className="mt-3">
            <ExitTrajectory form={form} />
          </div>

          <div className="mt-2 grid grid-cols-3 divide-x divide-white/[0.06] rounded-control border border-white/[0.06] bg-white/[0.02]">
            <div className="p-2.5">
              <div className="font-mono text-ui-title font-semibold tabular-nums text-blue-200">
                {displayPercent(form.targetProfitPct)}
              </div>
              <div className="mt-1 text-ui-caption font-semibold text-slate-300">
                收益武装
              </div>
              <p className="mt-0.5 text-ui-caption text-slate-600">
                达到后启动保护机制
              </p>
            </div>
            <div className="p-2.5">
              <div className="font-mono text-ui-title font-semibold tabular-nums text-blue-200">
                {displayPercent(form.baseFloorPct)}
              </div>
              <div className="mt-1 text-ui-caption font-semibold text-slate-300">
                初始保护
              </div>
              <p className="mt-0.5 text-ui-caption text-slate-600">
                启动后的初始保护位
              </p>
            </div>
            <div className="p-2.5">
              <div className="font-mono text-ui-title font-semibold tabular-nums text-blue-200">
                {displayPercent(form.initialGapPct)} →{' '}
                {displayPercent(form.maxGapPct)}
              </div>
              <div className="mt-1 text-ui-caption font-semibold text-slate-300">
                动态回撤
              </div>
              <p className="mt-0.5 text-ui-caption text-slate-600">
                随收益提升逐步放宽
              </p>
            </div>
          </div>

          <div className="mt-2 flex flex-wrap items-center justify-between gap-3 rounded-control border border-white/[0.06] bg-white/[0.02] p-2.5">
            <div className="flex min-w-0 items-center gap-2">
              <Sparkles
                className="h-4 w-4 shrink-0 text-primary"
                aria-hidden="true"
              />
              <div>
                <div className="flex items-center gap-2 text-ui-label font-semibold text-slate-200">
                  高利润保护
                  <span className="text-ui-caption text-emerald-300">
                    {form.highProfitLockEnabled ? '已启用' : '未启用'}
                  </span>
                </div>
                <p className="mt-0.5 text-ui-caption text-slate-500">
                  {form.highProfitLockEnabled
                    ? `收益超过 ${displayPercent(form.highProfitArmPct)} 后生效，峰值最大回吐 ${displayPercent(form.highProfitMaxDrawdownPct)}`
                    : '当前不限制高利润区的峰值回吐'}
                </p>
              </div>
            </div>
            {form.highProfitLockEnabled && (
              <div className="flex gap-3 font-mono text-ui-label tabular-nums text-slate-200">
                <span>{displayPercent(form.highProfitArmPct)} 武装</span>
                <span>
                  {displayPercent(form.highProfitMaxDrawdownPct)} 回吐
                </span>
              </div>
            )}
          </div>

          {trajectoryOpen && (
            <div
              id="t-trade-trajectory-editor"
              className="mt-2 rounded-control border border-primary/20 bg-primary/[0.035] p-3"
            >
              <div className="mb-3 text-ui-label font-semibold text-slate-200">
                动态退出参数
              </div>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 2xl:grid-cols-5">
                <ExecutionNumericField
                  id="t-trade-target"
                  label="收益武装线"
                  suffix="%"
                  value={form.targetProfitPct}
                  onChange={value => onFieldChange('targetProfitPct', value)}
                />
                <ExecutionNumericField
                  id="t-trade-floor"
                  label="初始保护线"
                  suffix="%"
                  value={form.baseFloorPct}
                  onChange={value => onFieldChange('baseFloorPct', value)}
                />
                <ExecutionNumericField
                  id="t-trade-initial-gap"
                  label="初始回撤宽度"
                  suffix="%"
                  value={form.initialGapPct}
                  onChange={value => onFieldChange('initialGapPct', value)}
                />
                <ExecutionNumericField
                  id="t-trade-max-gap"
                  label="最大回撤宽度"
                  suffix="%"
                  value={form.maxGapPct}
                  onChange={value => onFieldChange('maxGapPct', value)}
                />
                <ExecutionNumericField
                  id="t-trade-gap-slope"
                  label="放宽斜率"
                  value={form.trailingGapSlope}
                  onChange={value => onFieldChange('trailingGapSlope', value)}
                />
              </div>
            </div>
          )}
        </div>

        <div className="rounded-panel border border-white/[0.07] bg-[#0b1728] p-3 xl:col-span-5">
          <div>
            <h4 className="flex items-center gap-2 text-ui-title font-semibold text-slate-100">
              <ShieldCheck
                className="h-4 w-4 text-primary"
                aria-hidden="true"
              />
              风险护栏
            </h4>
            <p className="mt-1 text-ui-caption text-slate-500">
              控制异常风险，摘要常显，细节按需展开
            </p>
          </div>

          <div className="mt-3 space-y-2">
            <GuardrailShell
              enabled={form.highProfitLockEnabled}
              icon={Sparkles}
              id="high-profit"
              onOpenChange={toggleGuardrail}
              open={openGuardrail === 'high-profit'}
              summary={
                form.highProfitLockEnabled
                  ? `收益超过 ${displayPercent(form.highProfitArmPct)} 后生效，最大回吐 ${displayPercent(form.highProfitMaxDrawdownPct)}`
                  : '不限制高利润区的峰值回吐'
              }
              title="高利润保护"
              toggle={
                <EnabledSwitch
                  checked={form.highProfitLockEnabled}
                  label="启用高利润保护"
                  onCheckedChange={checked =>
                    onFieldChange('highProfitLockEnabled', checked)
                  }
                />
              }
            >
              {form.highProfitLockEnabled ? (
                <div className="grid grid-cols-2 gap-3">
                  <ExecutionNumericField
                    id="t-trade-high-profit-arm"
                    label="高利润武装线"
                    suffix="%"
                    value={form.highProfitArmPct}
                    onChange={value => onFieldChange('highProfitArmPct', value)}
                  />
                  <ExecutionNumericField
                    id="t-trade-high-profit-drawdown"
                    label="峰值最大回吐"
                    suffix="%"
                    value={form.highProfitMaxDrawdownPct}
                    onChange={value =>
                      onFieldChange('highProfitMaxDrawdownPct', value)
                    }
                  />
                </div>
              ) : (
                disabledEditorHint({ title: '高利润保护' })
              )}
            </GuardrailShell>

            <GuardrailShell
              enabled={form.rapidReversalEnabled}
              icon={Zap}
              id="rapid-reversal"
              onOpenChange={toggleGuardrail}
              open={openGuardrail === 'rapid-reversal'}
              summary={
                form.rapidReversalEnabled
                  ? `${displayNumber(form.rapidReversalWindowSeconds)} 秒内回吐 ${displayPercent(form.rapidReversalDrawdownPct)}，连续确认 ${displayNumber(form.rapidReversalConfirmTicks)} Tick`
                  : '不执行短时间峰值回吐紧急退出'
              }
              title="极速反转退出"
              tone="amber"
              toggle={
                <EnabledSwitch
                  checked={form.rapidReversalEnabled}
                  label="启用极速反转退出"
                  onCheckedChange={checked =>
                    onFieldChange('rapidReversalEnabled', checked)
                  }
                />
              }
            >
              <p className="mb-3 text-ui-caption leading-5 text-slate-500">
                高利润峰值形成后，短时间内连续确认买一收益快速回落即紧急退出。
              </p>
              {form.rapidReversalEnabled ? (
                <div className="grid grid-cols-3 gap-3">
                  <ExecutionNumericField
                    id="t-trade-rapid-reversal-window"
                    label="反转窗口"
                    suffix="秒"
                    value={form.rapidReversalWindowSeconds}
                    onChange={value =>
                      onFieldChange('rapidReversalWindowSeconds', value)
                    }
                  />
                  <ExecutionNumericField
                    id="t-trade-rapid-reversal-drawdown"
                    label="回吐阈值"
                    suffix="%"
                    value={form.rapidReversalDrawdownPct}
                    onChange={value =>
                      onFieldChange('rapidReversalDrawdownPct', value)
                    }
                  />
                  <ExecutionNumericField
                    id="t-trade-rapid-reversal-confirm"
                    label="连续确认"
                    suffix="Tick"
                    value={form.rapidReversalConfirmTicks}
                    onChange={value =>
                      onFieldChange('rapidReversalConfirmTicks', value)
                    }
                  />
                </div>
              ) : (
                disabledEditorHint({ title: '极速反转退出' })
              )}
            </GuardrailShell>

            <GuardrailShell
              enabled={form.limitUpTouchExitEnabled}
              icon={TrendingUp}
              id="limit-up"
              onOpenChange={toggleGuardrail}
              open={openGuardrail === 'limit-up'}
              summary={
                form.limitUpTouchExitEnabled
                  ? `触及涨停立即退出，容差 ${displayNumber(form.limitUpTouchToleranceTicks)} Tick`
                  : '触及涨停时不自动退出'
              }
              title="涨停触达退出"
              toggle={
                <EnabledSwitch
                  checked={form.limitUpTouchExitEnabled}
                  label="启用涨停触达退出"
                  onCheckedChange={checked =>
                    onFieldChange('limitUpTouchExitEnabled', checked)
                  }
                />
              }
            >
              {form.limitUpTouchExitEnabled ? (
                <div className="max-w-48">
                  <ExecutionNumericField
                    id="t-trade-limit-up-touch-tolerance"
                    label="涨停容差"
                    suffix="Tick"
                    value={form.limitUpTouchToleranceTicks}
                    onChange={value =>
                      onFieldChange('limitUpTouchToleranceTicks', value)
                    }
                  />
                </div>
              ) : (
                disabledEditorHint({ title: '涨停触达退出' })
              )}
            </GuardrailShell>

            <GuardrailShell
              enabled={form.hardStopEnabled}
              icon={AlertTriangle}
              id="hard-stop"
              onOpenChange={toggleGuardrail}
              open={openGuardrail === 'hard-stop'}
              summary={
                form.hardStopEnabled
                  ? `亏损达到 ${displayPercent(form.hardStopPct)} 时退出`
                  : '未设置硬止损阈值'
              }
              title="硬止损保护"
              tone="rose"
              toggle={
                <EnabledSwitch
                  checked={form.hardStopEnabled}
                  label="启用硬止损保护"
                  onCheckedChange={checked =>
                    onFieldChange('hardStopEnabled', checked)
                  }
                />
              }
            >
              {form.hardStopEnabled ? (
                <div className="max-w-48">
                  <ExecutionNumericField
                    id="t-trade-hard-stop"
                    label="硬止损线"
                    suffix="%"
                    value={form.hardStopPct}
                    onChange={value => onFieldChange('hardStopPct', value)}
                  />
                </div>
              ) : (
                disabledEditorHint({ title: '硬止损保护' })
              )}
            </GuardrailShell>

            <GuardrailShell
              icon={Clock3}
              id="time-exit"
              onOpenChange={toggleGuardrail}
              open={openGuardrail === 'time-exit'}
              summary={timeExitSummary(form)}
              title="时间退出"
            >
              <div className="space-y-3">
                <div className="space-y-1.5">
                  <Label
                    htmlFor="t-trade-time-exit-mode"
                    className="text-ui-label font-semibold text-slate-300"
                  >
                    时间退出策略
                  </Label>
                  <Select
                    value={form.timeExitMode}
                    onValueChange={value =>
                      onFieldChange(
                        'timeExitMode',
                        value as SettingsForm['timeExitMode']
                      )
                    }
                  >
                    <SelectTrigger
                      id="t-trade-time-exit-mode"
                      className="h-control-default rounded-control border-white/10 bg-[#07111f] text-ui-label focus:ring-primary/70"
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
                </div>

                {form.timeExitMode !== TTradeTimeExitMode.Unlimited && (
                  <div className="grid grid-cols-2 gap-3">
                    {form.timeExitMode ===
                      TTradeTimeExitMode.MaxHoldingDays && (
                      <ExecutionNumericField
                        id="t-trade-max-holding-days"
                        label="最长持有"
                        suffix="交易日"
                        value={form.maxHoldingTradingDays}
                        onChange={value =>
                          onFieldChange('maxHoldingTradingDays', value)
                        }
                      />
                    )}
                    <div className="space-y-1.5">
                      <Label
                        htmlFor="t-trade-time-exit-time"
                        className="text-ui-label font-semibold text-slate-300"
                      >
                        退出时刻
                      </Label>
                      <div className="relative">
                        <Input
                          id="t-trade-time-exit-time"
                          type="time"
                          value={form.timeExitTime}
                          onChange={event =>
                            onFieldChange('timeExitTime', event.target.value)
                          }
                          className="h-control-default rounded-control border-white/10 bg-[#07111f] font-mono text-ui-label focus-visible:ring-primary/70"
                        />
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </GuardrailShell>
          </div>

          {!form.hardStopEnabled &&
            form.timeExitMode === TTradeTimeExitMode.Unlimited && (
              <div className="mt-2 flex items-start gap-2 rounded-control border border-amber-400/15 bg-amber-400/[0.045] p-2.5 text-ui-caption leading-4 text-amber-100/80">
                <AlertTriangle
                  className="mt-0.5 h-3.5 w-3.5 shrink-0"
                  aria-hidden="true"
                />
                未达到收益武装线的批次可能长期持有，仍可通过人工操作结束。
              </div>
            )}
        </div>
      </div>
    </section>
  );
}
