import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/utils/cn';

import {
  supportedRequiredFields,
  supportedSessionCodes,
  type SignalPolicyLike,
} from './signalPolicy';
import type { SignalPolicyForm, SignalPolicyFormValue } from './types';

export type SignalPolicyPreviewLike = {
  valid: boolean;
  configVersion: number;
  errors: readonly { code: string; field?: string | null; message: string }[];
  warnings: readonly { code: string; field?: string | null; message: string }[];
  changedFields: readonly string[];
  requiresRewarm: boolean;
  normalizedPolicy?: {
    policyVersion: string;
    featureSchemaVersion: string;
  } | null;
};

type PolicyFieldProps = {
  field: keyof SignalPolicyForm;
  label: string;
  suffix?: string;
  value: string;
  inputMode?: 'decimal' | 'numeric' | 'text';
  onChange: (
    field: keyof SignalPolicyForm,
    value: SignalPolicyFormValue
  ) => void;
};

function PolicyField({
  field,
  label,
  suffix,
  value,
  inputMode = 'decimal',
  onChange,
}: PolicyFieldProps) {
  const id = `t-trade-policy-${field}`;
  return (
    <div className="space-y-1.5" data-policy-field={field}>
      <Label htmlFor={id} className="text-ui-caption font-bold text-slate-400">
        {label}
      </Label>
      <div className="relative">
        <Input
          id={id}
          inputMode={inputMode}
          value={value}
          onChange={event => onChange(field, event.target.value)}
          className={cn(
            'h-9 rounded-sm border-white/10 bg-[#07111f] font-mono text-ui-label focus-visible:ring-cyan-500/60',
            suffix && 'pr-14'
          )}
          aria-describedby={suffix ? `${id}-unit` : undefined}
        />
        {suffix && (
          <span
            id={`${id}-unit`}
            className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-ui-micro text-slate-600"
          >
            {suffix}
          </span>
        )}
      </div>
    </div>
  );
}

function Group({
  title,
  description,
  children,
  defaultOpen = false,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = React.useState(defaultOpen);
  return (
    <details
      className="group border border-white/[0.07] bg-[#07111f]/60"
      open={open}
      onToggle={event => setOpen(event.currentTarget.open)}
    >
      <summary className="cursor-pointer list-none border-b border-white/[0.05] p-ui-section marker:content-none">
        <h3 className="flex items-center justify-between text-ui-label font-black text-slate-200">
          {title}
          <span className="text-ui-micro text-cyan-300 group-open:hidden">
            展开
          </span>
          <span className="hidden text-ui-micro text-slate-500 group-open:inline">
            收起
          </span>
        </h3>
        <p className="mt-1 text-ui-micro leading-4 text-slate-600">
          {description}
        </p>
      </summary>
      <div className="grid grid-cols-2 gap-3 p-ui-section lg:grid-cols-3 2xl:grid-cols-4">
        {children}
      </div>
    </details>
  );
}

type StringPolicyFieldKey = {
  [Field in keyof SignalPolicyForm]: SignalPolicyForm[Field] extends string
    ? Field
    : never;
}[keyof SignalPolicyForm];

type PolicyEditorField =
  | {
      field: StringPolicyFieldKey;
      label: string;
      suffix?: string;
      kind?: 'number' | 'time';
    }
  | {
      field: 'momentumEnabled';
      label: string;
      kind: 'toggle';
    }
  | {
      field: 'pullbackRequiredFields' | 'momentumRequiredFields';
      label: string;
      kind: 'required-fields';
    }
  | {
      field: 'allowedSessionCodes';
      label: string;
      kind: 'sessions';
    };

type PolicyEditorGroup = {
  title: string;
  description: string;
  defaultOpen?: boolean;
  fields: readonly PolicyEditorField[];
};

const signalPolicyEditorGroups: readonly PolicyEditorGroup[] = [
  {
    title: '数据健康与状态窗口',
    description: '分别约束两条路径的样本、覆盖、必需字段、报价年龄与稀疏降级。',
    defaultOpen: true,
    fields: [
      { field: 'maxSamples', label: '窗口样本上限', suffix: '条' },
      { field: 'maxQuoteAgeMs', label: '报价最大年龄', suffix: 'ms' },
      { field: 'pullbackMinSamples', label: '回撤最少样本', suffix: '条' },
      {
        field: 'pullbackMinCoverageSeconds',
        label: '回撤最小覆盖',
        suffix: '秒',
      },
      { field: 'momentumMinSamples', label: '动量最少样本', suffix: '条' },
      {
        field: 'momentumMinCoverageSeconds',
        label: '动量最小覆盖',
        suffix: '秒',
      },
      {
        field: 'sparseDegradedGapSeconds',
        label: '稀疏降级间隔',
        suffix: '秒',
      },
      {
        field: 'pullbackRequiredFields',
        label: '回撤必需行情字段',
        kind: 'required-fields',
      },
      {
        field: 'momentumRequiredFields',
        label: '动量必需行情字段',
        kind: 'required-fields',
      },
    ],
  },
  {
    title: '交易时段与收盘保护',
    description:
      '时段由 Engine 分类；规则只在显式启用且可容纳完整候选生命周期的窗口内发射。',
    defaultOpen: true,
    fields: [
      {
        field: 'allowedSessionCodes',
        label: '允许的连续竞价时段',
        kind: 'sessions',
      },
      { field: 'continuousAmStartTime', label: '上午开始', kind: 'time' },
      { field: 'continuousAmEndTime', label: '上午结束', kind: 'time' },
      { field: 'continuousPmStartTime', label: '下午开始', kind: 'time' },
      { field: 'continuousPmEndTime', label: '下午结束', kind: 'time' },
      { field: 'closeProtectionSeconds', label: '收盘保护', suffix: '秒' },
    ],
  },
  {
    title: '回撤反弹路径',
    description: '回撤形成、低点稳定、反弹确认与量能短窗共同构成回撤 FSM。',
    defaultOpen: true,
    fields: [
      { field: 'pullbackLookbackSeconds', label: '回撤回看窗口', suffix: '秒' },
      {
        field: 'pullbackStabilizationSeconds',
        label: '低点稳定时长',
        suffix: '秒',
      },
      { field: 'pullbackThresholdPct', label: '最低回撤', suffix: '%' },
      {
        field: 'pullbackFormationThresholdMultiplier',
        label: '形成阶段倍率',
        suffix: '0–1',
      },
      { field: 'pullbackReboundThresholdPct', label: '最低反弹', suffix: '%' },
      { field: 'pullbackMaxSpreadTicks', label: '最大价差', suffix: 'tick' },
      {
        field: 'pullbackVolumeShortWindowSeconds',
        label: '量能短窗',
        suffix: '秒',
      },
      {
        field: 'pullbackVolumeBaselineWindowSeconds',
        label: '量能基线窗口',
        suffix: '秒',
      },
    ],
  },
  {
    title: '早期动量路径',
    description:
      '短窗涨幅、持续性、成交速度、VWAP 甜蜜区和流动性共同确认早期加速。',
    defaultOpen: true,
    fields: [
      { field: 'momentumEnabled', label: '启用动量路径', kind: 'toggle' },
      { field: 'momentumWindowSeconds', label: '动量短窗', suffix: '秒' },
      { field: 'momentumMinRisePct', label: '最低涨幅', suffix: '%' },
      {
        field: 'momentumFormationThresholdMultiplier',
        label: '形成阶段倍率',
        suffix: '0–1',
      },
      { field: 'momentumMinMoveSeconds', label: '最短持续', suffix: '秒' },
      { field: 'momentumBaselineSeconds', label: '成交基线窗口', suffix: '秒' },
      {
        field: 'momentumBaselineCoverageRatio',
        label: '基线覆盖率',
        suffix: '0–1',
      },
      {
        field: 'momentumMinAmountVelocityRatio',
        label: '成交速度倍率',
        suffix: '倍',
      },
      {
        field: 'momentumMinVwapPremiumPct',
        label: 'VWAP 甜蜜区下界',
        suffix: '%',
      },
      {
        field: 'momentumMaxVwapPremiumPct',
        label: 'VWAP 追涨上限',
        suffix: '%',
      },
      {
        field: 'momentumHighToleranceTicks',
        label: '高点容差',
        suffix: 'tick',
      },
      { field: 'momentumMaxSpreadTicks', label: '最大价差', suffix: 'tick' },
      { field: 'momentumMaxSpreadPct', label: '价差占比上限', suffix: '%' },
    ],
  },
  {
    title: 'D-1 画像安全夹取',
    description:
      '限制历史画像对实时阈值的影响范围，防止画像异常放大或压低规则。',
    fields: [
      {
        field: 'profilePullbackThresholdMinMultiplier',
        label: '回撤阈值倍率下界',
        suffix: '倍',
      },
      {
        field: 'profilePullbackThresholdMaxMultiplier',
        label: '回撤阈值倍率上界',
        suffix: '倍',
      },
      {
        field: 'profileMomentumRiseMinMultiplier',
        label: '动量涨幅倍率下界',
        suffix: '倍',
      },
      {
        field: 'profileMomentumRiseMaxMultiplier',
        label: '动量涨幅倍率上界',
        suffix: '倍',
      },
      {
        field: 'profileMomentumVelocityMinRatio',
        label: '成交速度倍率下界',
        suffix: '倍',
      },
      {
        field: 'profileMomentumVelocityMaxRatio',
        label: '成交速度倍率上界',
        suffix: '倍',
      },
    ],
  },
  {
    title: '正向贡献权重',
    description: '每条路径的七项权重必须各自精确合计为 100；硬门禁不参与加权。',
    fields: [
      { field: 'pullbackDepthWeight', label: '回撤·深度', suffix: '分' },
      { field: 'pullbackReboundWeight', label: '回撤·反弹', suffix: '分' },
      {
        field: 'pullbackStabilizationWeight',
        label: '回撤·稳定',
        suffix: '分',
      },
      {
        field: 'pullbackTurnSlopeWeight',
        label: '回撤·转折斜率',
        suffix: '分',
      },
      { field: 'pullbackVwapWeight', label: '回撤·VWAP', suffix: '分' },
      { field: 'pullbackLiquidityWeight', label: '回撤·流动性', suffix: '分' },
      { field: 'pullbackVolumeWeight', label: '回撤·量能', suffix: '分' },
      { field: 'momentumRiseWeight', label: '动量·涨幅', suffix: '分' },
      { field: 'momentumTurnoverWeight', label: '动量·成交速度', suffix: '分' },
      { field: 'momentumSlopeWeight', label: '动量·斜率', suffix: '分' },
      {
        field: 'momentumPersistenceWeight',
        label: '动量·持续性',
        suffix: '分',
      },
      { field: 'momentumVwapWeight', label: '动量·VWAP', suffix: '分' },
      { field: 'momentumLiquidityWeight', label: '动量·流动性', suffix: '分' },
      {
        field: 'momentumBookImbalanceWeight',
        label: '动量·盘口',
        suffix: '分',
      },
    ],
  },
  {
    title: '回撤评分归一化',
    description: '定义各回撤特征从原始观测值映射到贡献分的上下边界。',
    fields: [
      {
        field: 'pullbackDepthScoreMinPct',
        label: '深度·零分下界',
        suffix: '%',
      },
      {
        field: 'pullbackDepthScoreTargetMultiplier',
        label: '深度·目标倍率',
        suffix: '倍',
      },
      {
        field: 'pullbackReboundScoreMinPct',
        label: '反弹·零分下界',
        suffix: '%',
      },
      {
        field: 'pullbackReboundScoreMaxPct',
        label: '反弹·满分上界',
        suffix: '%',
      },
      {
        field: 'pullbackStabilizationScoreMinSeconds',
        label: '稳定·零分时长',
        suffix: '秒',
      },
      {
        field: 'pullbackStabilizationScoreMaxSeconds',
        label: '稳定·满分时长',
        suffix: '秒',
      },
      {
        field: 'pullbackTurnSlopeScoreMinPctPerSecond',
        label: '转折斜率·零分',
        suffix: '%/秒',
      },
      {
        field: 'pullbackTurnSlopeScoreMaxPctPerSecond',
        label: '转折斜率·满分',
        suffix: '%/秒',
      },
      {
        field: 'pullbackVwapFullScoreMaxPremiumPct',
        label: 'VWAP·满分溢价上界',
        suffix: '%',
      },
      {
        field: 'pullbackVwapZeroScorePremiumPct',
        label: 'VWAP·零分溢价',
        suffix: '%',
      },
      {
        field: 'pullbackLiquidityFullScoreSpreadTicks',
        label: '流动性·满分价差',
        suffix: 'tick',
      },
      {
        field: 'pullbackLiquidityZeroScoreSpreadTicks',
        label: '流动性·零分价差',
        suffix: 'tick',
      },
      {
        field: 'pullbackVolumeScoreMinRatio',
        label: '量能·零分倍率',
        suffix: '倍',
      },
      {
        field: 'pullbackVolumeScoreMaxRatio',
        label: '量能·满分倍率',
        suffix: '倍',
      },
    ],
  },
  {
    title: '动量评分归一化',
    description:
      '定义涨幅、成交速度、斜率、持续性、VWAP、流动性与盘口的计分边界。',
    fields: [
      { field: 'momentumRiseScoreMinPct', label: '涨幅·零分下界', suffix: '%' },
      {
        field: 'momentumRiseScoreTargetMultiplier',
        label: '涨幅·目标倍率',
        suffix: '倍',
      },
      {
        field: 'momentumTurnoverScoreMinRatio',
        label: '成交速度·零分倍率',
        suffix: '倍',
      },
      {
        field: 'momentumTurnoverScoreTargetMultiplier',
        label: '成交速度·目标倍率',
        suffix: '倍',
      },
      {
        field: 'momentumSlopeScoreMinPctPerSecond',
        label: '斜率·零分下界',
        suffix: '%/秒',
      },
      {
        field: 'momentumSlopeScoreTargetMultiplier',
        label: '斜率·目标倍率',
        suffix: '倍',
      },
      {
        field: 'momentumPersistenceScoreMinRatio',
        label: '持续性·零分比例',
        suffix: '0–1',
      },
      {
        field: 'momentumPersistenceScoreMaxRatio',
        label: '持续性·满分比例',
        suffix: '0–1',
      },
      {
        field: 'momentumVwapZeroScoreMinPremiumPct',
        label: 'VWAP·低位零分',
        suffix: '%',
      },
      {
        field: 'momentumVwapZeroScoreMaxPremiumPct',
        label: 'VWAP·高位零分',
        suffix: '%',
      },
      {
        field: 'momentumLiquidityFullScoreSpreadTicks',
        label: '流动性·满分价差',
        suffix: 'tick',
      },
      {
        field: 'momentumLiquidityZeroScoreSpreadTicks',
        label: '流动性·零分价差',
        suffix: 'tick',
      },
      {
        field: 'momentumBookImbalanceScoreMinRatio',
        label: '盘口·零分比例',
        suffix: '-1–1',
      },
      {
        field: 'momentumBookImbalanceScoreMaxRatio',
        label: '盘口·满分比例',
        suffix: '-1–1',
      },
    ],
  },
  {
    title: '显式诊断惩罚',
    description: '惩罚独立于正向权重展示；起点、满额边界和扣分上限均可审计。',
    fields: [
      {
        field: 'pullbackDataQualityPenaltyPoints',
        label: '回撤·数据降级扣分',
        suffix: '分',
      },
      {
        field: 'pullbackChasePenaltyStartPremiumPct',
        label: '回撤·追涨惩罚起点',
        suffix: '%',
      },
      {
        field: 'pullbackChasePenaltyFullPremiumPct',
        label: '回撤·追涨满额边界',
        suffix: '%',
      },
      {
        field: 'pullbackChasePenaltyPoints',
        label: '回撤·追涨最大扣分',
        suffix: '分',
      },
      {
        field: 'momentumDataQualityPenaltyPoints',
        label: '动量·数据降级扣分',
        suffix: '分',
      },
      {
        field: 'momentumOverextensionPenaltyStartPremiumPct',
        label: '动量·过度延伸起点',
        suffix: '%',
      },
      {
        field: 'momentumOverextensionPenaltyFullPremiumPct',
        label: '动量·过度延伸满额边界',
        suffix: '%',
      },
      {
        field: 'momentumOverextensionPenaltyPoints',
        label: '动量·过度延伸最大扣分',
        suffix: '分',
      },
    ],
  },
  {
    title: '阈值与候选生命周期',
    description:
      '四条分数阈值形成迟滞；确认、TTL 与再武装共同约束候选生命周期。',
    defaultOpen: true,
    fields: [
      { field: 'previewScore', label: '重点观察阈值', suffix: '分' },
      { field: 'candidateScore', label: '候选锁存阈值', suffix: '分' },
      { field: 'revalidateScore', label: '确认重验阈值', suffix: '分' },
      { field: 'rearmScore', label: '再武装阈值', suffix: '分' },
      { field: 'candidateConfirmSeconds', label: '候选确认停留', suffix: '秒' },
      {
        field: 'candidateConfirmTicks',
        label: '最少 source identity',
        suffix: '次',
      },
      { field: 'candidateTtlSeconds', label: '候选有效期', suffix: '秒' },
      { field: 'rearmSeconds', label: '再武装持续', suffix: '秒' },
    ],
  },
];

function PolicyEditorControl({
  field,
  form,
  onChange,
}: {
  field: PolicyEditorField;
  form: SignalPolicyForm;
  onChange: (
    field: keyof SignalPolicyForm,
    value: SignalPolicyFormValue
  ) => void;
}) {
  if (field.kind === 'toggle') {
    return (
      <label
        className="col-span-2 flex min-h-9 cursor-pointer items-center justify-between border border-white/[0.07] px-3 text-ui-caption text-slate-300 lg:col-span-1"
        data-policy-field={field.field}
      >
        {field.label}
        <input
          type="checkbox"
          checked={form[field.field]}
          onChange={event => onChange(field.field, event.target.checked)}
          className="h-4 w-4 accent-cyan-400"
        />
      </label>
    );
  }

  if (field.kind === 'required-fields' || field.kind === 'sessions') {
    const options =
      field.kind === 'required-fields'
        ? supportedRequiredFields
        : supportedSessionCodes;
    const selected = form[field.field];
    return (
      <fieldset
        className="col-span-2 border border-white/[0.07] p-3 lg:col-span-2"
        data-policy-field={field.field}
      >
        <legend className="px-1 text-ui-caption font-bold text-slate-400">
          {field.label}
        </legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {options.map(option => (
            <label
              key={option}
              className="flex cursor-pointer items-center gap-2 font-mono text-ui-micro text-slate-300"
            >
              <input
                type="checkbox"
                checked={selected.includes(option)}
                onChange={event =>
                  onChange(
                    field.field,
                    event.target.checked
                      ? [...selected, option]
                      : selected.filter(value => value !== option)
                  )
                }
                className="h-3.5 w-3.5 accent-cyan-400"
              />
              {option}
            </label>
          ))}
        </div>
      </fieldset>
    );
  }

  return (
    <PolicyField
      field={field.field}
      label={field.label}
      suffix={field.suffix}
      value={form[field.field]}
      inputMode={field.kind === 'time' ? 'text' : 'decimal'}
      onChange={onChange}
    />
  );
}

function policyDisplayValue(value: SignalPolicyFormValue | number | undefined) {
  if (Array.isArray(value)) return value.join(', ');
  return String(value ?? '—');
}

export function TTradeSignalPolicyEditor({
  conflictVersion,
  conflictPolicy,
  form,
  localErrors,
  onChange,
  onPreview,
  preview,
  previewLoading,
  serverConfigVersion,
}: {
  conflictVersion?: number | null;
  conflictPolicy?: SignalPolicyLike | null;
  form: SignalPolicyForm;
  localErrors: readonly string[];
  onChange: (
    field: keyof SignalPolicyForm,
    value: SignalPolicyFormValue
  ) => void;
  onPreview: () => void;
  preview?: SignalPolicyPreviewLike | null;
  previewLoading: boolean;
  serverConfigVersion: number;
}) {
  const conflictRows = conflictPolicy
    ? signalPolicyEditorGroups
        .flatMap(group => group.fields)
        .map(field => ({
          field: field.field,
          label: field.label,
          serverValue: policyDisplayValue(conflictPolicy[field.field]),
          draftValue: policyDisplayValue(form[field.field]),
        }))
        .filter(row => row.serverValue !== row.draftValue)
    : [];

  return (
    <div className="space-y-3">
      {conflictVersion != null && (
        <div
          role="alert"
          className="flex items-start gap-2 border border-rose-400/25 bg-rose-400/[0.08] p-3 text-ui-caption leading-4 text-rose-100"
        >
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="font-black">配置版本冲突，草稿已保留</div>
            <div className="mt-1 text-rose-200/75">
              你的草稿基于旧版本；服务端已更新到 v{conflictVersion}
              。请比较并重新验证，系统不会自动覆盖或合并交易参数。
            </div>
            {conflictPolicy && conflictRows.length > 0 && (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[520px] text-left text-ui-micro">
                  <caption className="sr-only">
                    服务端最新配置与当前本地草稿对比
                  </caption>
                  <thead className="text-rose-200/60">
                    <tr>
                      <th className="pb-1">参数</th>
                      <th>服务端 v{conflictVersion}</th>
                      <th>当前草稿</th>
                    </tr>
                  </thead>
                  <tbody>
                    {conflictRows.map(row => (
                      <tr
                        key={row.field}
                        className="border-t border-rose-200/10"
                      >
                        <td className="py-1.5">{row.label}</td>
                        <td className="font-mono">{row.serverValue}</td>
                        <td className="font-mono">{row.draftValue}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {conflictPolicy && conflictRows.length === 0 && (
              <div className="mt-2 text-ui-micro text-rose-200/65">
                100 个策略字段与服务端一致；冲突来自其他全局配置字段。
              </div>
            )}
          </div>
        </div>
      )}

      {signalPolicyEditorGroups.map(group => (
        <Group
          key={group.title}
          title={group.title}
          description={group.description}
          defaultOpen={group.defaultOpen}
        >
          {group.fields.map(field => (
            <PolicyEditorControl
              key={field.field}
              field={field}
              form={form}
              onChange={onChange}
            />
          ))}
        </Group>
      ))}

      <section
        className="border border-cyan-400/15 bg-cyan-400/[0.035] p-ui-section"
        aria-labelledby="t-trade-policy-preview-heading"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3
              id="t-trade-policy-preview-heading"
              className="text-ui-label font-black text-cyan-100"
            >
              服务端纯校验预览
            </h3>
            <p className="mt-1 text-ui-micro text-slate-600">
              基于配置版本 v{serverConfigVersion}
              ；预览不写数据库、不改变运行状态。
            </p>
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-control-compact rounded-sm border-cyan-400/20 text-ui-caption text-cyan-100"
            disabled={previewLoading || localErrors.length > 0}
            onClick={onPreview}
          >
            {previewLoading ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
            ) : (
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            )}
            验证配置
          </Button>
        </div>

        {localErrors.length > 0 && (
          <ul
            role="alert"
            className="mt-3 space-y-1 border-t border-rose-400/15 pt-3 text-ui-caption text-rose-200"
          >
            {localErrors.map(message => (
              <li key={message}>• {message}</li>
            ))}
          </ul>
        )}

        {preview && (
          <div
            aria-atomic="true"
            aria-live="polite"
            className="mt-3 border-t border-white/[0.06] pt-3"
            role="status"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={cn(
                  'inline-flex items-center gap-1.5 border px-2 py-1 text-ui-micro font-black',
                  preview.valid
                    ? 'border-emerald-400/20 bg-emerald-400/[0.07] text-emerald-200'
                    : 'border-rose-400/20 bg-rose-400/[0.07] text-rose-200'
                )}
              >
                {preview.valid ? (
                  <CheckCircle2 className="h-3.5 w-3.5" />
                ) : (
                  <AlertTriangle className="h-3.5 w-3.5" />
                )}
                {preview.valid ? '服务端校验通过' : '存在阻断错误'}
              </span>
              {preview.requiresRewarm && (
                <span className="inline-flex items-center gap-1.5 border border-amber-400/20 bg-amber-400/[0.07] px-2 py-1 text-ui-micro font-black text-amber-200">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  保存后需要重热
                </span>
              )}
              {preview.normalizedPolicy && (
                <span className="font-mono text-ui-micro text-slate-500">
                  {preview.normalizedPolicy.policyVersion} · feature{' '}
                  {preview.normalizedPolicy.featureSchemaVersion}
                </span>
              )}
            </div>
            {preview.requiresRewarm && (
              <p className="mt-2 text-ui-caption leading-4 text-amber-100">
                旧待确认信号会失效并重新进入
                WARMING；已真实成交批次、BucketLedger 与退出计划不受影响。
              </p>
            )}
            {preview.changedFields.length > 0 && (
              <p className="mt-2 text-ui-micro text-slate-500">
                规范化变更：{preview.changedFields.join('、')}
              </p>
            )}
            {preview.errors.length > 0 && (
              <ul className="mt-2 space-y-1 text-ui-caption text-rose-200">
                {preview.errors.map(issue => (
                  <li key={`${issue.code}:${issue.field || ''}`}>
                    {issue.field ? `${issue.field}：` : ''}
                    {issue.message}
                  </li>
                ))}
              </ul>
            )}
            {preview.warnings.length > 0 && (
              <ul className="mt-2 space-y-1 text-ui-caption text-amber-200">
                {preview.warnings.map(issue => (
                  <li key={`${issue.code}:${issue.field || ''}`}>
                    {issue.field ? `${issue.field}：` : ''}
                    {issue.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
