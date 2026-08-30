import {
  AlertTriangle,
  Copy,
  History,
  RotateCcw,
  Settings2,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

import type { ReplayCostForm } from './replaySettings';
import { localSignalPolicyErrors } from './signalPolicy';
import { TTradeExecutionSettingsPanel } from './TTradeExecutionSettingsPanel';
import { TTradeSignalPolicyEditor } from './TTradeSignalPolicyEditor';
import type {
  SettingsForm,
  SignalPolicyForm,
  SignalPolicyFormValue,
} from './types';

type SettingsFieldChange = <K extends keyof SettingsForm>(
  field: K,
  value: SettingsForm[K]
) => void;

function CostField({
  id,
  label,
  onChange,
  suffix,
  value,
}: {
  id: string;
  label: string;
  onChange: (value: string) => void;
  suffix: string;
  value: string;
}) {
  return (
    <div>
      <Label
        htmlFor={id}
        className="text-ui-label font-semibold text-slate-400"
      >
        {label}
      </Label>
      <div className="relative mt-1.5">
        <Input
          id={id}
          inputMode="decimal"
          value={value}
          onChange={event => onChange(event.target.value)}
          className="h-control-default rounded-control border-white/10 bg-[#07111f] pr-12 font-mono text-ui-label"
        />
        <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-ui-caption text-slate-600">
          {suffix}
        </span>
      </div>
    </div>
  );
}

function CompactField({
  id,
  label,
  onChange,
  suffix,
  value,
}: {
  id: string;
  label: string;
  onChange: (value: string) => void;
  suffix: string;
  value: string;
}) {
  return (
    <CostField
      id={id}
      label={label}
      onChange={onChange}
      suffix={suffix}
      value={value}
    />
  );
}

export function TTradeReplaySettingsEditor({
  costs,
  differenceCount,
  errors,
  form,
  liveConfigVersion,
  liveSettingsStale,
  onCostChange,
  onFieldChange,
  onRestore,
  onSignalPolicyChange,
  restoring,
}: {
  costs: ReplayCostForm;
  differenceCount: number;
  errors: readonly string[];
  form: SettingsForm;
  liveConfigVersion: number;
  liveSettingsStale: boolean;
  onCostChange: (field: keyof ReplayCostForm, value: string) => void;
  onFieldChange: SettingsFieldChange;
  onRestore: () => void;
  onSignalPolicyChange: (
    field: keyof SignalPolicyForm,
    value: SignalPolicyFormValue
  ) => void;
  restoring: boolean;
}) {
  const policyErrors = localSignalPolicyErrors(form.signalPolicy);
  return (
    <div className="flex h-full min-h-0 flex-col bg-[#081422]">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] px-ui-section py-3">
        <div>
          <div className="flex items-center gap-2">
            <Settings2 className="h-4 w-4 text-cyan-300" aria-hidden="true" />
            <h2 className="text-ui-body font-semibold text-slate-100">
              下次回测参数
            </h2>
            <span className="rounded-sm border border-blue-400/20 bg-primary/[0.08] px-1.5 py-0.5 text-ui-micro font-semibold text-blue-200">
              实盘 v{liveConfigVersion}
            </span>
            {differenceCount > 0 && (
              <span className="rounded-sm border border-amber-400/20 bg-amber-400/[0.07] px-1.5 py-0.5 text-ui-micro font-semibold text-amber-200">
                已调整 {differenceCount} 项
              </span>
            )}
          </div>
          <p className="mt-1 text-ui-caption text-slate-500">
            修改仅用于回测，不会保存或应用到实盘运行。
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={restoring}
          onClick={onRestore}
          className="h-control-compact rounded-control border-primary/30 text-ui-caption text-blue-100 hover:bg-primary/[0.08]"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          {restoring ? '正在读取…' : '还原当前实盘参数'}
        </Button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto custom-scrollbar">
        {liveSettingsStale && (
          <div className="flex items-start gap-2 border-b border-amber-400/20 bg-amber-400/[0.06] px-ui-section py-2 text-ui-caption text-amber-100">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            实盘配置已更新。当前回测草稿不会被自动覆盖；需要时请点击“还原当前实盘参数”。
          </div>
        )}
        {errors.length > 0 && (
          <div
            role="alert"
            className="border-b border-rose-400/20 bg-rose-400/[0.05] px-ui-section py-2.5"
          >
            <div className="flex items-center gap-2 text-ui-label font-semibold text-rose-100">
              <AlertTriangle className="h-3.5 w-3.5" />
              当前参数有 {errors.length} 项需要处理
            </div>
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-ui-caption text-rose-200">
              {errors.slice(0, 8).map(error => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="grid gap-px bg-white/[0.05] xl:grid-cols-2">
          <TTradeExecutionSettingsPanel
            form={form}
            onFieldChange={onFieldChange}
            showModeSelector={false}
          />
          <section className="bg-[#0a1424] p-ui-section xl:col-span-2">
            <div className="mb-3 border-b border-white/[0.05] pb-3">
              <h3 className="text-ui-label font-semibold text-slate-200">
                V3 有状态信号规则
              </h3>
              <p className="mt-1 text-ui-caption text-slate-600">
                回测草稿使用完整信号策略；启动时由 Engine 再次执行权威校验。
              </p>
            </div>
            <div className="mb-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
              <CompactField
                id="replay-price-deviation"
                label="确认价偏离"
                suffix="%"
                value={form.maxPriceDeviationPct}
                onChange={value => onFieldChange('maxPriceDeviationPct', value)}
              />
              <CompactField
                id="replay-cooldown"
                label="批次冷却时间"
                suffix="秒"
                value={form.cooldownSeconds}
                onChange={value => onFieldChange('cooldownSeconds', value)}
              />
            </div>
            <TTradeSignalPolicyEditor
              form={form.signalPolicy}
              localErrors={policyErrors}
              onChange={onSignalPolicyChange}
              onPreview={() => undefined}
              preview={null}
              previewLoading={false}
              serverConfigVersion={liveConfigVersion}
              showPreviewAction={false}
            />
          </section>
          <section className="bg-[#0a1424] p-ui-section xl:col-span-2">
            <div className="mb-3 border-b border-white/[0.05] pb-3">
              <h3 className="text-ui-label font-semibold text-slate-200">
                回测成本假设
              </h3>
              <p className="mt-1 text-ui-caption text-slate-600">
                百分比按成交金额计算；最低佣金按每笔买卖分别计收。
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
              <CostField
                id="replay-commission"
                label="佣金率"
                suffix="%"
                value={costs.commissionRatePct}
                onChange={value => onCostChange('commissionRatePct', value)}
              />
              <CostField
                id="replay-minimum-commission"
                label="最低佣金"
                suffix="元"
                value={costs.minimumCommission}
                onChange={value => onCostChange('minimumCommission', value)}
              />
              <CostField
                id="replay-stamp-tax"
                label="印花税率"
                suffix="%"
                value={costs.stampTaxRatePct}
                onChange={value => onCostChange('stampTaxRatePct', value)}
              />
              <CostField
                id="replay-transfer-fee"
                label="过户费率"
                suffix="%"
                value={costs.transferFeeRatePct}
                onChange={value => onCostChange('transferFeeRatePct', value)}
              />
              <CostField
                id="replay-slippage"
                label="滑点率"
                suffix="%"
                value={costs.slippageRatePct}
                onChange={value => onCostChange('slippageRatePct', value)}
              />
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

const frozenExecutionRows: Array<[keyof SettingsForm, string, string]> = [
  ['targetTradeAmount', '目标单次金额', '元'],
  ['maxTradeAmount', '单次金额上限', '元'],
  ['maxConcurrentBatches', '账户并发', '批'],
  ['maxTotalTExposurePct', '账户总 T 暴露', '%'],
  ['maxPriceDeviationPct', '确认价偏离', '%'],
  ['targetProfitPct', '止盈武装线', '%'],
  ['baseFloorPct', '初始保护线', '%'],
  ['initialGapPct', '初始回撤间距', '%'],
  ['trailingGapSlope', '回撤斜率', ''],
  ['maxGapPct', '最大回撤间距', '%'],
  ['highProfitLockEnabled', '高利润保护', ''],
  ['highProfitArmPct', '高利润武装线', '%'],
  ['highProfitMaxDrawdownPct', '高利润最大回吐', '%'],
  ['rapidReversalEnabled', '极速反转退出', ''],
  ['rapidReversalWindowSeconds', '极速反转窗口', '秒'],
  ['rapidReversalDrawdownPct', '极速反转回撤', '%'],
  ['rapidReversalConfirmTicks', '极速反转确认', ' Tick'],
  ['limitUpTouchExitEnabled', '涨停触达退出', ''],
  ['limitUpTouchToleranceTicks', '涨停触达容差', ' Tick'],
  ['hardStopEnabled', '硬止损保护', ''],
  ['hardStopPct', '硬止损', '%'],
  ['timeExitMode', '时间退出模式', ''],
  ['timeExitTime', '时间退出时刻', ''],
  ['maxHoldingTradingDays', '最长持有', '交易日'],
  ['cooldownSeconds', '批次冷却', '秒'],
];

function displayFrozenValue(value: unknown) {
  if (Array.isArray(value)) return value.join('、');
  if (typeof value === 'boolean') return value ? '启用' : '停用';
  return String(value ?? '--');
}

export function TTradeReplayFrozenSettings({
  costs,
  differenceCount,
  form,
  onCopy,
  onRestore,
  restoring,
}: {
  costs: ReplayCostForm;
  differenceCount: number;
  form: SettingsForm;
  onCopy: () => void;
  onRestore: () => void;
  restoring: boolean;
}) {
  const costRows = [
    ['佣金率', costs.commissionRatePct, '%'],
    ['最低佣金', costs.minimumCommission, '元'],
    ['印花税率', costs.stampTaxRatePct, '%'],
    ['过户费率', costs.transferFeeRatePct, '%'],
    ['滑点率', costs.slippageRatePct, '%'],
  ];
  return (
    <div className="flex h-full min-h-0 flex-col bg-[#081422]">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] px-ui-section py-3">
        <div>
          <div className="flex items-center gap-2">
            <History className="h-4 w-4 text-cyan-300" />
            <h2 className="text-ui-body font-semibold text-slate-100">
              冻结参数快照
            </h2>
            <span className="rounded-sm border border-slate-400/20 bg-white/[0.04] px-1.5 py-0.5 text-ui-micro text-slate-300">
              与参考实盘差异 {differenceCount} 项
            </span>
          </div>
          <p className="mt-1 text-ui-caption text-slate-500">
            该快照来自本次回测运行实例，历史记录不可修改。
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onCopy}
            className="h-control-compact rounded-control border-primary/30 text-ui-caption text-blue-100"
          >
            <Copy className="h-3.5 w-3.5" />
            复制为新回测参数
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={restoring}
            onClick={onRestore}
            className="h-control-compact rounded-control border-white/10 text-ui-caption text-slate-300"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            还原当前实盘参数
          </Button>
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-ui-section custom-scrollbar">
        <section className="border border-white/[0.06] bg-[#0a1424]">
          <div className="border-b border-white/[0.05] px-ui-section py-2.5 text-ui-label font-semibold text-slate-200">
            执行、资金与退出参数
          </div>
          <div className="grid gap-px bg-white/[0.05] sm:grid-cols-2 xl:grid-cols-4">
            {frozenExecutionRows.map(([field, label, suffix]) => (
              <div key={field} className="bg-[#07111f] p-3">
                <div className="text-ui-caption text-slate-600">{label}</div>
                <div className="mt-1 font-mono text-ui-label text-slate-200">
                  {displayFrozenValue(form[field])} {suffix}
                </div>
              </div>
            ))}
          </div>
        </section>
        <section className="mt-3 border border-white/[0.06] bg-[#0a1424]">
          <div className="border-b border-white/[0.05] px-ui-section py-2.5 text-ui-label font-semibold text-slate-200">
            回测成本假设
          </div>
          <div className="grid gap-px bg-white/[0.05] sm:grid-cols-2 xl:grid-cols-5">
            {costRows.map(([label, value, suffix]) => (
              <div key={label} className="bg-[#07111f] p-3">
                <div className="text-ui-caption text-slate-600">{label}</div>
                <div className="mt-1 font-mono text-ui-label text-slate-200">
                  {value} {suffix}
                </div>
              </div>
            ))}
          </div>
        </section>
        <details className="mt-3 border border-white/[0.06] bg-[#0a1424]" open>
          <summary className="cursor-pointer px-ui-section py-2.5 text-ui-label font-semibold text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/70">
            V3 信号规则 · {Object.keys(form.signalPolicy).length} 项
          </summary>
          <div className="grid gap-px border-t border-white/[0.05] bg-white/[0.05] sm:grid-cols-2 xl:grid-cols-3">
            {Object.entries(form.signalPolicy).map(([field, value]) => (
              <div key={field} className="bg-[#07111f] p-3">
                <div className="font-mono text-ui-caption text-slate-600">
                  {field}
                </div>
                <div className="mt-1 break-words font-mono text-ui-label text-slate-200">
                  {displayFrozenValue(value)}
                </div>
              </div>
            ))}
          </div>
        </details>
      </div>
    </div>
  );
}
