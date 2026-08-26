import { zodResolver } from '@hookform/resolvers/zod';
import {
  AlertCircle,
  ChevronDown,
  Plus,
  ShieldCheck,
  Trash2,
} from 'lucide-react';
import * as React from 'react';
import { Controller, useFieldArray, useForm, useWatch } from 'react-hook-form';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/utils/cn';

import {
  defaultEntryPlanDraft,
  entryPlanDraftSchema,
  formatEntryCurrency,
  getEntryPlanPrimaryActionLabel,
  getEntryPlanSaveAction,
} from '../model/draft';
import type {
  EntryPlanController,
  EntryPlanCapabilitiesView,
  EntryPlanDraft,
  EntrySecurityOption,
} from '../model/types';

import { EntryStrategyPicker } from './EntryStrategyPicker';

const targetModes = [
  {
    value: 'TARGET_POSITION_PCT',
    title: '目标仓位',
    description: '买到组合总仓位目标，只补当前与目标之间的缺口。',
  },
  {
    value: 'INCREMENTAL_AMOUNT_CNY',
    title: '新增金额',
    description: '限定本计划累计新增投入金额。',
  },
  {
    value: 'ADDITIONAL_VOLUME',
    title: '新增股数',
    description: '限定本计划累计新增股数，最终订单仍按 A 股规则规范化。',
  },
] as const;

const executionScenarios = [
  {
    value: 'PAPER_AUTO',
    title: '模拟托管',
    description: '自动监控和模拟执行，不产生券商买单。',
  },
  {
    value: 'LIVE_MANUAL',
    title: '实盘逐笔确认',
    description: '命中规则后进入待确认，每笔重新获取价格与风控。',
  },
  {
    value: 'LIVE_AUTO',
    title: '实盘自动托管',
    description: '需完成设备与额度绑定的限时授权，配置变化后重新授权。',
  },
] as const;

function Field({
  children,
  error,
  htmlFor,
  label,
  unit,
}: {
  children: React.ReactNode;
  error?: string;
  htmlFor: string;
  label: string;
  unit?: string;
}) {
  return (
    <div className="min-w-0">
      <label
        className="mb-1.5 block text-ui-label font-bold text-slate-300"
        htmlFor={htmlFor}
      >
        {label}
        {unit ? (
          <span className="ml-1 font-normal text-slate-500">({unit})</span>
        ) : null}
      </label>
      {children}
      {error ? (
        <p className="mt-1 text-ui-caption text-rose-300">{error}</p>
      ) : null}
    </div>
  );
}

function Section({
  children,
  number,
  title,
}: {
  children: React.ReactNode;
  number: number;
  title: string;
}) {
  return (
    <section className="rounded-lg border border-white/10 bg-[#0b1120]/75 p-ui-section">
      <h2 className="mb-4 flex items-center gap-2 text-ui-body font-black text-slate-100">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/10 font-mono text-ui-caption text-primary">
          {number}
        </span>
        {title}
      </h2>
      {children}
    </section>
  );
}

export function EntryPlanEditor({
  controller,
  initialDraft,
  onSecuritySelected,
  selectedSecurity,
  capabilities,
}: {
  controller: EntryPlanController;
  initialDraft?: Partial<EntryPlanDraft>;
  onSecuritySelected: (security: EntrySecurityOption) => void;
  selectedSecurity: EntrySecurityOption | null;
  capabilities?: EntryPlanCapabilitiesView;
}) {
  const [saving, setSaving] = React.useState(false);
  const [submitError, setSubmitError] = React.useState<string | null>(null);
  const [submitMessage, setSubmitMessage] = React.useState<string | null>(null);
  const {
    control,
    formState: { errors },
    handleSubmit,
    register,
    setValue,
  } = useForm<EntryPlanDraft>({
    defaultValues: { ...defaultEntryPlanDraft, ...initialDraft },
    resolver: zodResolver(entryPlanDraftSchema),
  });
  const {
    append: appendLadderLevel,
    fields: ladderFields,
    remove: removeLadderLevel,
    replace: replaceLadderLevels,
  } = useFieldArray({
    control,
    name: 'priceLadderLevels',
    keyName: 'formKey',
  });
  const editingExistingPlan = Boolean(initialDraft?.planId);

  React.useEffect(() => {
    if (!selectedSecurity) return;
    setValue('instrumentCode', selectedSecurity.instrumentCode, {
      shouldValidate: true,
    });
    setValue('instrumentName', selectedSecurity.instrumentName, {
      shouldValidate: true,
    });
    if (
      !editingExistingPlan &&
      selectedSecurity.latestPrice &&
      selectedSecurity.latestPrice > 0
    ) {
      // Quotes arrive as GraphQL floats. Normalize the editor seed so binary
      // floating-point tails (for example 123.07000000000001) never leak into
      // a user-visible hard price boundary. Three decimals also cover ETFs.
      setValue('maxBuyPrice', Number(selectedSecurity.latestPrice.toFixed(3)), {
        shouldValidate: true,
      });
    }
  }, [editingExistingPlan, selectedSecurity, setValue]);

  const draft = useWatch({ control });
  const executionScenario = draft.executionScenario ?? 'PAPER_AUTO';
  const targetMode = draft.targetMode ?? 'TARGET_POSITION_PCT';
  const strategy = draft.strategy ?? 'TREND_PULLBACK_CONFIRMATION';
  const exitProtectionEnabled = draft.exitProtectionEnabled ?? false;
  const ruleCapability = capabilities?.ruleTypes.find(
    item => item.ruleType === strategy
  );
  const visiblePresets =
    ruleCapability !== undefined
      ? ruleCapability.presets
      : [
          { presetId: 'CONSERVATIVE', label: '稳健', summary: '' },
          { presetId: 'BALANCED', label: '均衡', summary: '' },
          { presetId: 'ACTIVE', label: '积极', summary: '' },
        ];
  const capabilityField = (key: string) =>
    ruleCapability?.fields?.find(item => item.key === key);
  const visibleTargetModes =
    capabilities?.targetModes && capabilities.targetModes.length > 0
      ? capabilities.targetModes.flatMap(server => {
          const local = targetModes.find(
            option => option.value === server.value
          );
          return local
            ? [
                {
                  ...local,
                  title: server.label,
                  description: server.description,
                },
              ]
            : [];
        })
      : targetModes;

  React.useEffect(() => {
    if (strategy !== 'PRICE_LADDER') return;
    const maxBuyPrice = Number(draft.maxBuyPrice ?? 0);
    if (maxBuyPrice <= 0 || ladderFields.length > 0) return;
    const count = Math.max(1, Math.min(Number(draft.trancheCount ?? 4), 8));
    const amount = Math.min(
      Number(draft.maxSingleIntentAmountCny ?? 0),
      Number(draft.maxTotalAmountCny ?? 0) / count
    );
    replaceLadderLevels(
      Array.from({ length: count }, (_, index) => ({
        levelId: `ladder-${index + 1}`,
        triggerPrice: Number((maxBuyPrice * (1 - index * 0.01)).toFixed(2)),
        trancheMode: 'AMOUNT',
        trancheAmountCny: Math.max(100, Math.floor(amount / 100) * 100),
        trancheVolume: 0,
      }))
    );
  }, [
    draft.maxBuyPrice,
    draft.maxSingleIntentAmountCny,
    draft.maxTotalAmountCny,
    draft.trancheCount,
    ladderFields.length,
    replaceLadderLevels,
    strategy,
  ]);

  function applyPreset(presetId: string, parameters?: Record<string, unknown>) {
    if (
      presetId === 'CONSERVATIVE' ||
      presetId === 'BALANCED' ||
      presetId === 'ACTIVE'
    ) {
      setValue('preset', presetId, { shouldDirty: true });
    }
    const values = parameters ?? {};
    const mappings = [
      ['fast_ema_period', 'fastEmaPeriod'],
      ['slow_ema_period', 'slowEmaPeriod'],
      ['pullback_pct', 'pullbackPct'],
      ['rebound_pct', 'reboundPct'],
    ] as const;
    mappings.forEach(([source, target]) => {
      const value = Number(values[source]);
      if (Number.isFinite(value)) {
        setValue(target, value, { shouldDirty: true, shouldValidate: true });
      }
    });
  }

  async function submit(values: EntryPlanDraft, paused: boolean) {
    setSaving(true);
    setSubmitError(null);
    setSubmitMessage(null);
    const action = getEntryPlanSaveAction(values.executionScenario, paused);
    try {
      await controller.saveDraft(values, action);
      setSubmitMessage(
        paused
          ? '计划已保存并保持暂停，不会生成买入意图。'
          : values.executionScenario === 'LIVE_AUTO'
            ? '授权预览已请求；完成挑战前计划不会启动。'
            : '计划已保存并按所选执行方式启动。'
      );
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : '保存计划失败');
    } finally {
      setSaving(false);
    }
  }

  const targetDescription =
    visibleTargetModes.find(option => option.value === targetMode)
      ?.description ?? '';

  return (
    <form className="space-y-3" onSubmit={event => event.preventDefault()}>
      <div
        className="rounded-lg border border-emerald-400/15 bg-[#080d18] p-ui-section lg:hidden"
        aria-live="polite"
      >
        <h2 className="text-ui-body font-black text-slate-100">计划摘要</h2>
        <div className="mt-3 grid grid-cols-2 gap-2 text-ui-label">
          <span className="text-slate-500">目标方式</span>
          <span className="text-right text-slate-200">
            {visibleTargetModes.find(item => item.value === targetMode)?.title}
          </span>
          <span className="text-slate-500">累计预算</span>
          <span className="text-right font-mono text-slate-200">
            {formatEntryCurrency(Number(draft.maxTotalAmountCny ?? 0))}
          </span>
          <span className="text-slate-500">单笔上限</span>
          <span className="text-right font-mono text-slate-200">
            {formatEntryCurrency(Number(draft.maxSingleIntentAmountCny ?? 0))}
          </span>
          <span className="text-slate-500">最高买价</span>
          <span className="text-right font-mono text-slate-200">
            ¥{Number(draft.maxBuyPrice ?? 0).toFixed(2)}
          </span>
        </div>
      </div>

      <Section number={1} title="买什么">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            error={errors.instrumentCode?.message}
            htmlFor="entry-plan-instrument"
            label="股票"
          >
            <Input
              id="entry-plan-instrument"
              readOnly
              value={
                selectedSecurity?.instrumentCode ?? draft.instrumentCode ?? ''
              }
              placeholder="请从左侧搜索证券"
              onClick={() => {
                if (selectedSecurity) onSecuritySelected(selectedSecurity);
              }}
            />
          </Field>
          <Field htmlFor="entry-plan-bucket-core" label="买入归因仓">
            <Controller
              control={control}
              name="bucket"
              render={({ field }) => (
                <div
                  aria-label="买入归因仓"
                  className="grid grid-cols-2 gap-2"
                  role="radiogroup"
                >
                  {[
                    ['core', '核心仓'],
                    ['swing', '活跃仓'],
                  ].map(([value, label]) => (
                    <button
                      aria-checked={field.value === value}
                      className={cn(
                        'min-h-11 cursor-pointer rounded-lg border px-3 text-ui-label font-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                        field.value === value
                          ? 'border-primary/40 bg-primary/10 text-primary'
                          : 'border-white/10 bg-white/[0.025] text-slate-400 hover:border-white/20'
                      )}
                      disabled={editingExistingPlan}
                      id={`entry-plan-bucket-${value}`}
                      key={value}
                      onClick={() => field.onChange(value)}
                      role="radio"
                      type="button"
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}
            />
            {editingExistingPlan ? (
              <p className="mt-1 text-ui-caption leading-4 text-slate-600">
                已有计划的归因仓不可原地变更；如需换仓，请取消后重新创建。
              </p>
            ) : null}
          </Field>
        </div>
        {selectedSecurity ? (
          <p className="mt-3 text-ui-label text-slate-400">
            {selectedSecurity.instrumentName} · 当前持仓{' '}
            {selectedSecurity.heldVolume.toLocaleString('zh-CN')} 股 · 最新价{' '}
            {selectedSecurity.latestPrice
              ? `¥${selectedSecurity.latestPrice.toFixed(2)}`
              : '--'}
          </p>
        ) : null}
      </Section>

      <Section number={2} title="买到多少">
        <Controller
          control={control}
          name="targetMode"
          render={({ field }) => (
            <div
              aria-label="目标方式"
              className="grid gap-2 sm:grid-cols-3"
              role="radiogroup"
            >
              {visibleTargetModes.map(option => (
                <button
                  aria-checked={field.value === option.value}
                  className={cn(
                    'min-h-11 cursor-pointer rounded-lg border p-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                    field.value === option.value
                      ? 'border-primary/40 bg-primary/10'
                      : 'border-white/10 bg-white/[0.025] hover:border-primary/25'
                  )}
                  key={option.value}
                  onClick={() => field.onChange(option.value)}
                  role="radio"
                  type="button"
                >
                  <span className="block text-ui-label font-black text-slate-100">
                    {option.title}
                  </span>
                  <span className="mt-1 block text-ui-caption leading-4 text-slate-500">
                    {option.description}
                  </span>
                </button>
              ))}
            </div>
          )}
        />
        <p className="mt-2 text-ui-caption text-slate-500">
          {targetDescription}
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {targetMode === 'TARGET_POSITION_PCT' ? (
            <Field
              error={errors.targetPositionPct?.message}
              htmlFor="entry-plan-target-position"
              label="目标总仓位"
              unit="%"
            >
              <Input
                id="entry-plan-target-position"
                type="number"
                step="0.1"
                {...register('targetPositionPct')}
              />
            </Field>
          ) : targetMode === 'INCREMENTAL_AMOUNT_CNY' ? (
            <Field
              error={errors.incrementalAmountCny?.message}
              htmlFor="entry-plan-incremental-amount"
              label="计划新增金额"
              unit="元"
            >
              <Input
                id="entry-plan-incremental-amount"
                type="number"
                step="100"
                {...register('incrementalAmountCny')}
              />
            </Field>
          ) : (
            <Field
              error={errors.additionalVolume?.message}
              htmlFor="entry-plan-additional-volume"
              label="计划新增股数"
              unit="股"
            >
              <Input
                id="entry-plan-additional-volume"
                type="number"
                step="100"
                {...register('additionalVolume')}
              />
            </Field>
          )}
          <Field
            error={errors.maxTotalAmountCny?.message}
            htmlFor="entry-plan-max-budget"
            label="累计预算硬上限"
            unit="元"
          >
            <Input
              id="entry-plan-max-budget"
              type="number"
              step="100"
              {...register('maxTotalAmountCny')}
            />
          </Field>
          <Field
            error={errors.maxPositionPct?.message}
            htmlFor="entry-plan-max-position"
            label="绝对仓位硬上限"
            unit="%"
          >
            <Input
              id="entry-plan-max-position"
              type="number"
              step="0.1"
              {...register('maxPositionPct')}
            />
          </Field>
          <Field
            error={errors.maxBuyPrice?.message}
            htmlFor="entry-plan-max-price"
            label="最高可买价"
            unit="元"
          >
            <Input
              id="entry-plan-max-price"
              type="number"
              step="0.001"
              {...register('maxBuyPrice')}
            />
          </Field>
        </div>
      </Section>

      <Section number={3} title="什么时候买">
        <Controller
          control={control}
          name="strategy"
          render={({ field }) => (
            <EntryStrategyPicker
              capabilities={capabilities?.ruleTypes}
              onChange={field.onChange}
              value={field.value}
            />
          )}
        />
        {visiblePresets.length > 0 ? (
          <div className="mt-3 grid gap-2 sm:grid-cols-3">
            {visiblePresets.map(preset => (
              <button
                aria-pressed={draft.preset === preset.presetId}
                className={cn(
                  'min-h-11 cursor-pointer rounded-lg border px-3 py-2 text-left text-ui-label focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                  draft.preset === preset.presetId
                    ? 'border-primary/40 bg-primary/10 text-primary'
                    : 'border-white/10 bg-white/[0.025] text-slate-300 hover:border-primary/25'
                )}
                key={preset.presetId}
                onClick={() => applyPreset(preset.presetId, preset.parameters)}
                type="button"
              >
                <span className="block font-bold">{preset.label}</span>
                {preset.summary ? (
                  <span className="mt-1 block text-ui-caption leading-4 text-slate-500">
                    {preset.summary}
                  </span>
                ) : null}
              </button>
            ))}
          </div>
        ) : null}
        {strategy === 'PRICE_LADDER' ? (
          <div className="mt-3 rounded-lg border border-cyan-400/15 bg-cyan-400/[0.035] p-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <h3 className="text-ui-label font-black text-slate-100">
                  {capabilityField('levels')?.label ?? '可见价格档位'}
                </h3>
                <p className="mt-1 text-ui-caption leading-4 text-slate-500">
                  {capabilityField('levels')?.helpText ??
                    '到价后一次只处理一档；每档真实成交后才会累计，连续行情不会重复计为成交。'}
                </p>
              </div>
              <Button
                size="sm"
                type="button"
                variant="outline"
                onClick={() => {
                  const previous =
                    draft.priceLadderLevels?.[
                      Math.max(0, (draft.priceLadderLevels?.length ?? 1) - 1)
                    ];
                  appendLadderLevel({
                    levelId: `ladder-${Date.now()}`,
                    triggerPrice: Number(
                      (
                        Number(
                          previous?.triggerPrice ?? draft.maxBuyPrice ?? 0
                        ) * 0.99
                      ).toFixed(2)
                    ),
                    trancheAmountCny: Number(
                      previous?.trancheAmountCny ??
                        draft.maxSingleIntentAmountCny ??
                        0
                    ),
                    trancheMode: 'AMOUNT',
                    trancheVolume: 0,
                  });
                }}
              >
                <Plus />
                添加档位
              </Button>
            </div>
            <div className="mt-3 space-y-2">
              {ladderFields.map((field, index) => (
                <div
                  className="grid items-end gap-2 rounded-md border border-white/10 bg-[#080d18]/70 p-2 sm:grid-cols-[1fr_auto_1fr_auto]"
                  key={field.formKey}
                >
                  <Field
                    error={
                      errors.priceLadderLevels?.[index]?.triggerPrice?.message
                    }
                    htmlFor={`entry-plan-ladder-price-${index}`}
                    label={`档位 ${index + 1} 触发价`}
                    unit="元"
                  >
                    <Input
                      id={`entry-plan-ladder-price-${index}`}
                      type="number"
                      step={capabilityField('levels')?.step ?? 0.01}
                      {...register(`priceLadderLevels.${index}.triggerPrice`)}
                    />
                  </Field>
                  <Controller
                    control={control}
                    name={`priceLadderLevels.${index}.trancheMode`}
                    render={({ field: modeField }) => (
                      <div
                        aria-label={`档位 ${index + 1} 批次单位`}
                        className="grid grid-cols-2 rounded-md border border-white/10 p-0.5"
                        role="radiogroup"
                      >
                        {[
                          ['AMOUNT', '金额'],
                          ['VOLUME', '股数'],
                        ].map(([value, label]) => (
                          <button
                            aria-checked={modeField.value === value}
                            className={cn(
                              'min-h-10 rounded px-2 text-ui-caption font-bold',
                              modeField.value === value
                                ? 'bg-primary/10 text-primary'
                                : 'text-slate-500'
                            )}
                            key={value}
                            onClick={() => modeField.onChange(value)}
                            role="radio"
                            type="button"
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                    )}
                  />
                  {draft.priceLadderLevels?.[index]?.trancheMode ===
                  'VOLUME' ? (
                    <Field
                      error={
                        errors.priceLadderLevels?.[index]?.trancheVolume
                          ?.message
                      }
                      htmlFor={`entry-plan-ladder-volume-${index}`}
                      label="本档股数"
                      unit="股"
                    >
                      <Input
                        id={`entry-plan-ladder-volume-${index}`}
                        type="number"
                        step="100"
                        {...register(
                          `priceLadderLevels.${index}.trancheVolume`
                        )}
                      />
                    </Field>
                  ) : (
                    <Field
                      error={
                        errors.priceLadderLevels?.[index]?.trancheAmountCny
                          ?.message
                      }
                      htmlFor={`entry-plan-ladder-amount-${index}`}
                      label="本档预算"
                      unit="元"
                    >
                      <Input
                        id={`entry-plan-ladder-amount-${index}`}
                        type="number"
                        step="100"
                        {...register(
                          `priceLadderLevels.${index}.trancheAmountCny`
                        )}
                      />
                    </Field>
                  )}
                  <Button
                    aria-label={`删除档位 ${index + 1}`}
                    disabled={ladderFields.length <= 1}
                    size="icon"
                    type="button"
                    variant="ghost"
                    onClick={() => removeLadderLevel(index)}
                  >
                    <Trash2 />
                  </Button>
                </div>
              ))}
            </div>
            {errors.priceLadderLevels?.message ? (
              <p className="mt-2 text-ui-caption text-rose-300">
                {errors.priceLadderLevels.message}
              </p>
            ) : null}
          </div>
        ) : strategy === 'TREND_PULLBACK_CONFIRMATION' ? (
          <details className="mt-3 rounded-lg border border-white/10 bg-white/[0.02] p-3">
            <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between text-ui-label font-black text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
              专业参数 · {capabilities?.version ?? '等待能力契约'}
              <ChevronDown
                aria-hidden="true"
                className="h-4 w-4 text-slate-500"
              />
            </summary>
            <p className="mb-3 text-ui-caption leading-4 text-slate-500">
              字段含义和取值范围来自服务端能力契约；预设也会同步填写这些参数。
            </p>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {[
                {
                  key: 'fast_ema_period',
                  name: 'fastEmaPeriod',
                  fallbackLabel: '快线周期',
                  fallbackUnit: '交易日',
                  error: errors.fastEmaPeriod?.message,
                },
                {
                  key: 'slow_ema_period',
                  name: 'slowEmaPeriod',
                  fallbackLabel: '慢线周期',
                  fallbackUnit: '交易日',
                  error: errors.slowEmaPeriod?.message,
                },
                {
                  key: 'pullback_pct',
                  name: 'pullbackPct',
                  fallbackLabel: '观察回撤',
                  fallbackUnit: '%',
                  error: errors.pullbackPct?.message,
                },
                {
                  key: 'rebound_pct',
                  name: 'reboundPct',
                  fallbackLabel: '企稳反弹',
                  fallbackUnit: '%',
                  error: errors.reboundPct?.message,
                },
              ].map(item => {
                const metadata = capabilityField(item.key);
                return (
                  <div key={item.key}>
                    <Field
                      error={item.error}
                      htmlFor={`entry-plan-${item.key}`}
                      label={metadata?.label ?? item.fallbackLabel}
                      unit={metadata?.unit ?? item.fallbackUnit}
                    >
                      <Input
                        id={`entry-plan-${item.key}`}
                        max={metadata?.max ?? undefined}
                        min={metadata?.min ?? undefined}
                        step={metadata?.step ?? 1}
                        type="number"
                        {...register(
                          item.name as
                            | 'fastEmaPeriod'
                            | 'slowEmaPeriod'
                            | 'pullbackPct'
                            | 'reboundPct'
                        )}
                      />
                    </Field>
                    {metadata?.helpText ? (
                      <p className="mt-1 text-ui-caption leading-4 text-slate-600">
                        {metadata.helpText}
                      </p>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </details>
        ) : (
          <p className="mt-3 rounded-md border border-white/10 bg-white/[0.02] p-3 text-ui-caption leading-5 text-slate-500">
            人工触发不会绕过风控。点击“立即检查”只会提出本批意图；实盘模式仍按所选授权方式处理。
          </p>
        )}
      </Section>

      <Section number={4} title="怎么分批">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <Field
            error={errors.trancheCount?.message}
            htmlFor="entry-plan-tranches"
            label="计划批次数"
            unit="批"
          >
            <Input
              id="entry-plan-tranches"
              type="number"
              {...register('trancheCount')}
            />
          </Field>
          <Field
            error={errors.maxSingleIntentAmountCny?.message}
            htmlFor="entry-plan-single-cap"
            label="单笔金额上限"
            unit="元"
          >
            <Input
              id="entry-plan-single-cap"
              type="number"
              step="100"
              {...register('maxSingleIntentAmountCny')}
            />
          </Field>
          <Field
            error={errors.maxDailyFilledAmountCny?.message}
            htmlFor="entry-plan-daily-cap"
            label="单日成交上限"
            unit="元"
          >
            <Input
              id="entry-plan-daily-cap"
              type="number"
              step="100"
              {...register('maxDailyFilledAmountCny')}
            />
          </Field>
          <Field
            error={errors.minIntervalMinutes?.message}
            htmlFor="entry-plan-interval"
            label="最小触发间隔"
            unit="分钟"
          >
            <Input
              id="entry-plan-interval"
              type="number"
              {...register('minIntervalMinutes')}
            />
          </Field>
          <Field
            error={errors.cashBufferPct?.message}
            htmlFor="entry-plan-cash-buffer"
            label="现金缓冲"
            unit="%"
          >
            <Input
              id="entry-plan-cash-buffer"
              type="number"
              step="0.1"
              {...register('cashBufferPct')}
            />
          </Field>
        </div>
      </Section>

      <Section number={5} title="成交后怎么办">
        <label className="flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border border-white/10 bg-white/[0.025] p-3">
          <input
            className="mt-0.5"
            type="checkbox"
            {...register('exitProtectionEnabled')}
          />
          <span>
            <span className="block text-ui-label font-black text-slate-100">
              成交后创建独立卖出保护计划
            </span>
            <span className="mt-1 block text-ui-caption leading-4 text-slate-500">
              每个真实买入批次独立保护；取消买入计划不会卖出已买股份，也不会取消已激活保护。
            </span>
          </span>
        </label>
        {errors.exitProtectionEnabled?.message ? (
          <p className="mt-2 text-ui-caption text-rose-300">
            {errors.exitProtectionEnabled.message}
          </p>
        ) : null}
        {exitProtectionEnabled ? (
          <div className="mt-3">
            <p className="mb-3 text-ui-caption leading-5 text-slate-500">
              至少填写一项。保护计划只在真实买入成交后按实际成交批次创建；下单回执和委托已报不会创建保护数量。
            </p>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              <Field
                error={errors.exitStopPrice?.message}
                htmlFor="entry-plan-exit-stop-price"
                label="绝对止损价"
                unit="元；0 为关闭"
              >
                <Input
                  id="entry-plan-exit-stop-price"
                  min="0"
                  step="0.01"
                  type="number"
                  {...register('exitStopPrice')}
                />
              </Field>
              <Field
                error={errors.exitGrossTakeProfitPct?.message}
                htmlFor="entry-plan-exit-take-profit"
                label="收益止盈"
                unit="%；0 为关闭"
              >
                <Input
                  id="entry-plan-exit-take-profit"
                  min="0"
                  step="0.1"
                  type="number"
                  {...register('exitGrossTakeProfitPct')}
                />
              </Field>
              <Field
                error={errors.exitMaxHoldingDays?.message}
                htmlFor="entry-plan-exit-max-days"
                label="最大持有日"
                unit="交易日；0 为关闭"
              >
                <Input
                  id="entry-plan-exit-max-days"
                  min="0"
                  step="1"
                  type="number"
                  {...register('exitMaxHoldingDays')}
                />
              </Field>
              <Field
                error={errors.exitTrailingArmProfitPct?.message}
                htmlFor="entry-plan-exit-trailing-arm"
                label="追踪保护启动收益"
                unit="%"
              >
                <Input
                  id="entry-plan-exit-trailing-arm"
                  min="0"
                  step="0.1"
                  type="number"
                  {...register('exitTrailingArmProfitPct')}
                />
              </Field>
              <Field
                error={errors.exitTrailingDrawdownPct?.message}
                htmlFor="entry-plan-exit-trailing-drawdown"
                label="追踪回撤幅度"
                unit="%"
              >
                <Input
                  id="entry-plan-exit-trailing-drawdown"
                  min="0"
                  step="0.1"
                  type="number"
                  {...register('exitTrailingDrawdownPct')}
                />
              </Field>
            </div>
          </div>
        ) : null}
      </Section>

      <Section number={6} title="如何执行">
        <Controller
          control={control}
          name="executionScenario"
          render={({ field }) => (
            <div
              aria-label="执行方式"
              className="grid gap-2 sm:grid-cols-3"
              role="radiogroup"
            >
              {executionScenarios.map(option => (
                <button
                  aria-checked={field.value === option.value}
                  className={cn(
                    'min-h-11 cursor-pointer rounded-lg border p-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                    field.value === option.value
                      ? 'border-primary/40 bg-primary/10'
                      : 'border-white/10 bg-white/[0.025] hover:border-primary/25'
                  )}
                  key={option.value}
                  onClick={() => field.onChange(option.value)}
                  role="radio"
                  type="button"
                >
                  <span className="block text-ui-label font-black text-slate-100">
                    {option.title}
                  </span>
                  <span className="mt-1 block text-ui-caption leading-4 text-slate-500">
                    {option.description}
                  </span>
                </button>
              ))}
            </div>
          )}
        />
      </Section>

      {Object.keys(errors).length > 0 ? (
        <div
          className="flex items-start gap-2 rounded-lg border border-rose-400/25 bg-rose-400/10 p-3 text-ui-label text-rose-100"
          role="alert"
        >
          <AlertCircle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
          请检查股票、目标和所有硬风险边界。系统不会提交不完整计划。
        </div>
      ) : null}
      {submitError ? (
        <p className="text-ui-label text-rose-300" role="alert">
          {submitError}
        </p>
      ) : null}
      {submitMessage ? (
        <p className="text-ui-label text-emerald-200" aria-live="polite">
          {submitMessage}
        </p>
      ) : null}

      <div className="flex flex-col-reverse gap-2 border-t border-white/10 pt-4 sm:flex-row sm:justify-end">
        <Button
          className="min-h-control-large"
          disabled={saving}
          type="button"
          variant="outline"
          onClick={handleSubmit(values => submit(values, true))}
        >
          保存并保持暂停
        </Button>
        <Button
          className="min-h-control-large"
          disabled={saving}
          type="button"
          variant="success"
          onClick={handleSubmit(values => submit(values, false))}
        >
          <ShieldCheck />
          {getEntryPlanPrimaryActionLabel(executionScenario)}
        </Button>
      </div>
    </form>
  );
}
