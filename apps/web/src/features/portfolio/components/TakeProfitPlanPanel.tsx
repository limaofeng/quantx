import {
  Activity,
  ChevronDown,
  CheckCircle2,
  Clock,
  Gauge,
  Loader2,
  Power,
  Radar,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  Target,
  Trash2,
} from 'lucide-react';
import * as React from 'react';

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
import type { ConditionalLiquidationOrdersQuery as ConditionalLiquidationOrdersQueryData } from '@/generated/gql/graphql';
import { useToast } from '@/hooks/use-toast';
import { financialToneClass } from '@/shared/utils/financialColors';
import { cn } from '@/utils/cn';
import { formatCurrency, formatPercent } from '@/utils/transform/data';

import type { Position } from '../types';
import {
  buildTakeProfitPlanPreview,
  calculateProfitPctFromTargetPrice,
  calculateTargetPrice,
  getSellableVolume,
  takeProfitStrategyTemplates,
  toFiniteNumber,
  type TakeProfitSellMode,
  type TakeProfitStrategyId,
  type TakeProfitTriggerMode,
} from '../utils/takeProfitPlan';

type ConditionalLiquidationOrderView = NonNullable<
  ConditionalLiquidationOrdersQueryData['conditionalLiquidationOrders']
>[number];

export type ConditionalLiquidationFormPayload = {
  accountId?: string;
  autoExitAuthorized: boolean;
  dynamicPolicy?: Record<string, unknown> | null;
  enabled: boolean;
  executionMode: string;
  id?: string;
  instrumentName?: string | null;
  remark?: string | null;
  sellMode: string;
  sellRatioPct?: number | null;
  sellVolume?: number | null;
  strategy: string;
  stockCode: string;
  targetPrice?: number | null;
  targetProfitPct?: number | null;
};

interface TakeProfitPlanPanelProps {
  accountId?: string;
  actionLoading: boolean;
  holding?: Position | null;
  isLoading: boolean;
  onCancel: (orderId: string) => Promise<void>;
  onEvaluate: () => Promise<void>;
  onSave: (payload: ConditionalLiquidationFormPayload) => Promise<void>;
  onToggleEnabled: (orderId: string, enabled: boolean) => Promise<void>;
  order?: ConditionalLiquidationOrderView | null;
  selectedStockCode: string;
}

const supportedStrategyIds = new Set<TakeProfitStrategyId>([
  'IMMEDIATE',
  'PARTIAL_TRAILING',
]);

function numericInput(value: unknown) {
  const amount = toFiniteNumber(value);
  return amount === null ? '' : String(amount);
}

function parseOptionalNumber(value: string) {
  const text = value.trim();
  if (!text) return null;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatDateTime(value: unknown) {
  if (typeof value === 'number') {
    return new Date(value * 1000).toLocaleString('zh-CN');
  }

  if (typeof value !== 'string' || !value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN');
}

function formatPrice(value: unknown) {
  const amount = toFiniteNumber(value);
  return amount === null || amount <= 0 ? '--' : formatCurrency(amount);
}

function formatPercentOrDash(value: unknown, signed = false) {
  const amount = toFiniteNumber(value);
  if (amount === null) return '--';
  return `${signed && amount > 0 ? '+' : ''}${formatPercent(amount)}`;
}

function formatDistance(value: number | null) {
  if (value === null) return '待设置';
  if (value <= 0) return '已达触发线';
  return `还差 ${formatPercent(value)}`;
}

function getConditionalOrderStatus(
  order?: ConditionalLiquidationOrderView | null
) {
  if (!order) return { label: '未配置', tone: 'text-slate-400' };
  if (order.status === 'SUBMITTED') {
    return { label: '委托待成交', tone: 'text-market-down' };
  }
  if (order.status === 'PARTIALLY_EXITED') {
    return { label: '部分成交后跟踪', tone: 'text-blue-200' };
  }
  if (order.status === 'COMPLETED') {
    return { label: '保护数量已卖出', tone: 'text-market-down' };
  }
  if (order.status === 'FAILED') {
    return { label: '提交失败', tone: 'text-rose-300' };
  }
  if (order.status === 'CANCELLED') {
    return { label: '已取消', tone: 'text-slate-500' };
  }
  return order.enabled
    ? { label: '监控中', tone: 'text-red-200' }
    : { label: '已停用', tone: 'text-amber-200' };
}

function PlanMetric({
  label,
  tone,
  toneClassName,
  value,
}: {
  label: string;
  tone?: 'danger' | 'muted' | 'success';
  toneClassName?: string;
  value: string;
}) {
  return (
    <div className="min-w-0 rounded-md border border-white/5 bg-white/[0.025] px-3 py-2.5">
      <div className="truncate text-ui-caption font-black uppercase tracking-wider text-slate-600">
        {label}
      </div>
      <div
        className={cn(
          'mt-1 truncate font-mono text-ui-body font-black tabular-nums text-slate-100',
          tone === 'danger' && 'text-rose-300',
          tone === 'success' && 'text-emerald-300',
          tone === 'muted' && 'text-slate-400',
          toneClassName
        )}
      >
        {value}
      </div>
    </div>
  );
}

function SectionTitle({
  icon: Icon,
  label,
}: {
  icon: React.ElementType;
  label: string;
}) {
  return (
    <div className="flex items-center gap-2 text-ui-label font-black text-slate-200">
      <Icon className="h-3.5 w-3.5 text-red-300" />
      {label}
    </div>
  );
}

export function TakeProfitPlanPanel({
  accountId,
  actionLoading,
  holding,
  isLoading,
  onCancel,
  onEvaluate,
  onSave,
  onToggleEnabled,
  order,
  selectedStockCode,
}: TakeProfitPlanPanelProps) {
  const { toast } = useToast();
  const [strategyId, setStrategyId] =
    React.useState<TakeProfitStrategyId>('IMMEDIATE');
  const [triggerMode, setTriggerMode] =
    React.useState<TakeProfitTriggerMode>('PROFIT');
  const [targetProfitPct, setTargetProfitPct] = React.useState('15');
  const [targetPrice, setTargetPrice] = React.useState('');
  const [sellMode, setSellMode] =
    React.useState<TakeProfitSellMode>('ALL_AVAILABLE');
  const [sellRatioPct, setSellRatioPct] = React.useState('50');
  const [sellVolume, setSellVolume] = React.useState('');
  const [executionMode, setExecutionMode] = React.useState('paper');
  const [autoExitAuthorized, setAutoExitAuthorized] = React.useState(false);
  const [strategyPickerOpen, setStrategyPickerOpen] = React.useState(false);

  React.useEffect(() => {
    const hasAdaptiveStrategy =
      order?.strategy === 'ADAPTIVE_VOLUME_PRICE_TRAILING';
    const hasProfitTarget = toFiniteNumber(order?.targetProfitPct) !== null;
    const hasPriceTarget = toFiniteNumber(order?.targetPrice) !== null;

    setStrategyId(hasAdaptiveStrategy ? 'PARTIAL_TRAILING' : 'IMMEDIATE');
    setTriggerMode(
      hasProfitTarget && hasPriceTarget
        ? 'EITHER'
        : hasPriceTarget
          ? 'PRICE'
          : 'PROFIT'
    );
    setTargetProfitPct(numericInput(order?.targetProfitPct) || '15');
    setTargetPrice(numericInput(order?.targetPrice));
    setSellMode((order?.sellMode as TakeProfitSellMode) || 'ALL_AVAILABLE');
    setSellRatioPct(numericInput(order?.sellRatioPct) || '50');
    setSellVolume(numericInput(order?.sellVolume));
    setExecutionMode(order?.executionMode || 'paper');
    setAutoExitAuthorized(Boolean(order?.autoExitAuthorized));
  }, [
    order?.id,
    order?.sellMode,
    order?.sellRatioPct,
    order?.sellVolume,
    order?.targetPrice,
    order?.targetProfitPct,
    order?.strategy,
    order?.executionMode,
    order?.autoExitAuthorized,
    order?.updatedAt,
  ]);

  const selectedTemplate =
    takeProfitStrategyTemplates.find(template => template.id === strategyId) ||
    takeProfitStrategyTemplates[0];
  const isSaveSupported = supportedStrategyIds.has(strategyId);
  const status = getConditionalOrderStatus(order);
  const isTerminal =
    order?.status === 'SUBMITTED' ||
    order?.status === 'COMPLETED' ||
    order?.status === 'CANCELLED';
  const canOperate = Boolean(selectedStockCode) && !actionLoading;
  const canUpdateExisting = Boolean(
    order?.id && !isTerminal && order.status !== 'PARTIALLY_EXITED'
  );
  const existingId = canUpdateExisting ? order?.id : undefined;
  const sellableVolume = getSellableVolume(holding);
  const parsedProfitPct = parseOptionalNumber(targetProfitPct);
  const parsedTargetPrice = parseOptionalNumber(targetPrice);
  const parsedSellRatioPct = parseOptionalNumber(sellRatioPct);
  const parsedSellVolume = parseOptionalNumber(sellVolume);
  const preview = buildTakeProfitPlanPreview({
    holding,
    sellMode,
    sellRatioPct: sellMode === 'PERCENT_AVAILABLE' ? parsedSellRatioPct : null,
    sellVolume: sellMode === 'FIXED_VOLUME' ? parsedSellVolume : null,
    targetPrice: triggerMode === 'PROFIT' ? null : parsedTargetPrice,
    targetProfitPct: triggerMode === 'PRICE' ? null : parsedProfitPct,
    triggerMode,
  });
  const derivedTargetPrice =
    triggerMode === 'PROFIT'
      ? calculateTargetPrice(holding?.avgPrice, parsedProfitPct)
      : null;
  const derivedTargetProfitPct =
    triggerMode === 'PRICE'
      ? calculateProfitPctFromTargetPrice(holding?.avgPrice, parsedTargetPrice)
      : null;

  const handleStrategySelect = (nextStrategyId: TakeProfitStrategyId) => {
    const nextTemplate = takeProfitStrategyTemplates.find(
      template => template.id === nextStrategyId
    );
    if (!nextTemplate || nextTemplate.status !== 'supported') return;

    setStrategyId(nextStrategyId);
    setStrategyPickerOpen(false);
    if (nextStrategyId === 'PARTIAL_TRAILING') {
      setTriggerMode('PROFIT');
      setTargetProfitPct(value => value || '15');
      setSellMode('PERCENT_AVAILABLE');
      setSellRatioPct(value => value || '50');
      return;
    }

    setTargetProfitPct(value => value || '15');
    if (!order?.id) setSellMode('ALL_AVAILABLE');
  };

  const applyPreset = (
    profitPct: string,
    mode: TakeProfitSellMode,
    ratioPct?: string
  ) => {
    setStrategyId(mode === 'ALL_AVAILABLE' ? 'IMMEDIATE' : 'PARTIAL_TRAILING');
    setStrategyPickerOpen(false);
    setTriggerMode('PROFIT');
    setTargetProfitPct(profitPct);
    setTargetPrice('');
    setSellMode(mode);
    if (ratioPct) setSellRatioPct(ratioPct);
  };

  const handleSave = async (nextEnabled: boolean) => {
    if (!isSaveSupported) {
      toast({
        title: '高级策略尚未接入监控引擎',
        description: '当前只能保存到价即止盈和首段分批止盈。',
        variant: 'destructive',
      });
      return;
    }

    const profitPct =
      triggerMode === 'PRICE' ? null : parseOptionalNumber(targetProfitPct);
    const price =
      triggerMode === 'PROFIT' ? null : parseOptionalNumber(targetPrice);
    const ratioPct = parseOptionalNumber(sellRatioPct);
    const fixedVolume = parseOptionalNumber(sellVolume);

    if (
      (triggerMode === 'PROFIT' && profitPct === null) ||
      (triggerMode === 'PRICE' && price === null) ||
      (triggerMode === 'EITHER' && profitPct === null && price === null)
    ) {
      toast({
        title: '缺少止盈触发条件',
        description: '请填写目标收益率或目标价。',
        variant: 'destructive',
      });
      return;
    }
    if (sellMode === 'PERCENT_AVAILABLE' && (!ratioPct || ratioPct <= 0)) {
      toast({
        title: '卖出比例无效',
        description: '按比例卖出时，请填写 0 到 100 之间的比例。',
        variant: 'destructive',
      });
      return;
    }
    if (sellMode === 'FIXED_VOLUME' && (!fixedVolume || fixedVolume <= 0)) {
      toast({
        title: '固定股数无效',
        description: '按股数卖出时，请填写大于 0 的股数。',
        variant: 'destructive',
      });
      return;
    }
    const adaptiveVolume = Math.trunc(preview.estimatedSellVolume);
    if (
      strategyId === 'PARTIAL_TRAILING' &&
      (adaptiveVolume <= 0 || adaptiveVolume >= sellableVolume)
    ) {
      toast({
        title: '动态止盈必须保护部分持仓',
        description: '计划股数需大于 0 且小于当前可卖数量。',
        variant: 'destructive',
      });
      return;
    }
    if (
      strategyId === 'PARTIAL_TRAILING' &&
      executionMode === 'live' &&
      !autoExitAuthorized
    ) {
      toast({
        title: '需要自动卖出授权',
        description: '实盘动态止盈会自动提交卖单，请先勾选明确授权。',
        variant: 'destructive',
      });
      return;
    }

    await onSave({
      accountId,
      autoExitAuthorized,
      dynamicPolicy: {},
      enabled: nextEnabled,
      executionMode,
      id: existingId,
      instrumentName: holding?.instrumentName || null,
      remark:
        strategyId === 'PARTIAL_TRAILING'
          ? '平衡型量价动态止盈；固定保护数量，逐笔成交回填'
          : null,
      sellMode: strategyId === 'PARTIAL_TRAILING' ? 'FIXED_VOLUME' : sellMode,
      sellRatioPct:
        strategyId !== 'PARTIAL_TRAILING' && sellMode === 'PERCENT_AVAILABLE'
          ? ratioPct
          : null,
      sellVolume:
        strategyId === 'PARTIAL_TRAILING'
          ? adaptiveVolume
          : sellMode === 'FIXED_VOLUME' && fixedVolume !== null
            ? Math.trunc(fixedVolume)
            : null,
      strategy:
        strategyId === 'PARTIAL_TRAILING'
          ? 'ADAPTIVE_VOLUME_PRICE_TRAILING'
          : 'IMMEDIATE',
      stockCode: selectedStockCode,
      targetPrice: price,
      targetProfitPct: profitPct,
    });
  };

  return (
    <section
      className="min-w-0 rounded-md border border-white/5 bg-[#0b1120]/70"
      data-testid="take-profit-plan-panel"
    >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/5 px-3 py-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <ShieldCheck className="h-3.5 w-3.5 text-market-up" />
            <h3 className="truncate text-ui-label font-black text-slate-100">
              止盈计划
            </h3>
            <span
              className={cn(
                'rounded border border-white/10 bg-white/[0.04] px-2 py-1 text-ui-caption font-black',
                status.tone
              )}
            >
              {status.label}
            </span>
            {selectedTemplate.status === 'preview' && (
              <span
                className="rounded border border-amber-400/20 bg-amber-500/10 px-2 py-1 text-ui-caption font-black text-amber-100"
                role="alert"
              >
                待接入监控引擎
              </span>
            )}
          </div>
          <p className="mt-1 text-ui-caption font-bold text-slate-600">
            动态模式进入止盈区后继续观察量价，成交状态以券商回报为准。
          </p>
          <p className="mt-1 text-ui-caption font-bold text-slate-600">
            最近检查 {formatDateTime(order?.lastCheckedAt)}
            {order?.lastError ? ` · ${order.lastError}` : ''}
          </p>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            title="立即检查条件，满足时提交止盈委托"
            disabled={!order?.id || isTerminal || actionLoading}
            onClick={onEvaluate}
            className="h-control-compact px-2 text-ui-caption font-black"
          >
            <Target className="mr-1.5 h-3.5 w-3.5" />
            立即检查
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            title={order?.enabled ? '停用止盈计划' : '启用止盈计划'}
            disabled={!order?.id || isTerminal || actionLoading}
            onClick={() =>
              order?.id && onToggleEnabled(order.id, !order.enabled)
            }
            className="h-8 px-2 text-ui-caption font-black"
          >
            <Power className="mr-1.5 h-3.5 w-3.5" />
            {order?.enabled ? '停用' : '启用'}
          </Button>
        </div>
      </div>

      <div className="space-y-ui-section p-3">
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-5">
          <PlanMetric label="当前价" value={formatPrice(holding?.lastPrice)} />
          <PlanMetric label="成本价" value={formatPrice(holding?.avgPrice)} />
          <PlanMetric
            label="当前收益率"
            toneClassName={financialToneClass(
              preview.currentProfitPct,
              'holding'
            )}
            value={formatPercentOrDash(preview.currentProfitPct, true)}
          />
          <PlanMetric label="目标价" value={formatPrice(preview.targetPrice)} />
          <PlanMetric
            label="距目标"
            tone={
              preview.triggerDistancePct !== null &&
              preview.triggerDistancePct <= 0
                ? 'success'
                : 'muted'
            }
            value={formatDistance(preview.triggerDistancePct)}
          />
        </div>

        <div className="space-y-2">
          <SectionTitle icon={Radar} label="止盈策略" />
          <div
            className="rounded-md border border-white/5 bg-[#08101d]/75 p-3"
            data-testid="take-profit-strategy-selector"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-ui-label font-black text-slate-100">
                    {selectedTemplate.label}
                  </span>
                  <span
                    className={cn(
                      'rounded border px-1.5 py-0.5 text-ui-micro font-black',
                      isSaveSupported
                        ? 'border-emerald-400/20 bg-emerald-500/10 text-emerald-200'
                        : 'border-amber-400/20 bg-amber-500/10 text-amber-200'
                    )}
                  >
                    {isSaveSupported ? '可保存' : '待接入监控引擎'}
                  </span>
                </div>
                <p className="mt-1 text-ui-caption font-bold leading-4 text-slate-400">
                  {selectedTemplate.description}
                </p>
                <p className="mt-1 text-ui-caption font-bold leading-4 text-slate-600">
                  {selectedTemplate.summary}
                </p>
              </div>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-control-compact shrink-0 gap-1 border border-white/10 bg-white/[0.025] px-2 text-ui-caption font-black text-slate-300 hover:border-primary/25 hover:bg-primary/10 hover:text-primary"
                onClick={() => setStrategyPickerOpen(open => !open)}
                aria-expanded={strategyPickerOpen}
              >
                <ChevronDown
                  className={cn(
                    'h-3.5 w-3.5 transition-transform',
                    strategyPickerOpen && 'rotate-180'
                  )}
                />
                {strategyPickerOpen ? '收起策略库' : '展开策略库'}
              </Button>
            </div>

            {strategyPickerOpen && (
              <div className="mt-3 max-h-[260px] overflow-y-auto pr-1">
                <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                  {takeProfitStrategyTemplates.map(template => {
                    const selected = template.id === strategyId;
                    const supported = template.status === 'supported';

                    return (
                      <button
                        key={template.id}
                        type="button"
                        disabled={!supported}
                        onClick={() => handleStrategySelect(template.id)}
                        data-testid={`take-profit-template-${template.id}`}
                        className={cn(
                          'min-h-[86px] rounded-md border px-3 py-2 text-left transition-colors',
                          selected
                            ? 'border-primary/40 bg-primary/10 text-primary'
                            : 'border-white/5 bg-white/[0.025] text-slate-300 hover:border-primary/25 hover:bg-primary/10',
                          !supported &&
                            'cursor-not-allowed border-white/5 bg-white/[0.015] text-slate-600 hover:border-white/5 hover:bg-white/[0.015]'
                        )}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <span className="text-ui-label font-black">
                            {template.label}
                          </span>
                          <span
                            className={cn(
                              'shrink-0 rounded border px-1.5 py-0.5 text-ui-micro font-black',
                              supported
                                ? 'border-emerald-400/20 bg-emerald-500/10 text-emerald-200'
                                : 'border-amber-400/20 bg-amber-500/10 text-amber-200'
                            )}
                          >
                            {supported ? '可保存' : '待接入监控引擎'}
                          </span>
                        </div>
                        <p className="mt-1 text-ui-caption font-bold leading-4">
                          {template.description}
                        </p>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="grid gap-3 xl:grid-cols-[1.1fr_0.9fr]">
          <div className="space-y-3 rounded-md border border-white/5 bg-[#08101d]/75 p-3">
            <SectionTitle icon={Gauge} label="止盈目标" />
            <div className="flex flex-wrap gap-2">
              {[
                ['PROFIT', '按收益率止盈'],
                ['PRICE', '按目标价止盈'],
                ['EITHER', '任一条件触发'],
              ].map(([mode, label]) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setTriggerMode(mode as TakeProfitTriggerMode)}
                  className={cn(
                    'h-8 rounded-md border px-3 text-ui-caption font-black transition-colors',
                    triggerMode === mode
                      ? 'border-primary/35 bg-primary/10 text-primary'
                      : 'border-white/10 bg-white/[0.025] text-slate-500 hover:border-primary/25 hover:text-primary'
                  )}
                >
                  {label}
                </button>
              ))}
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              {triggerMode !== 'PRICE' && (
                <div className="grid gap-1.5">
                  <Label
                    htmlFor="take-profit-target-profit"
                    className="text-ui-caption font-black text-slate-500"
                  >
                    目标收益率 (%)
                  </Label>
                  <Input
                    id="take-profit-target-profit"
                    type="number"
                    inputMode="decimal"
                    value={targetProfitPct}
                    onChange={event => setTargetProfitPct(event.target.value)}
                    className="h-9 rounded-md border-white/10 bg-[#050b14] text-ui-label font-bold text-slate-100"
                  />
                  {derivedTargetPrice !== null && (
                    <div className="text-ui-caption font-bold text-slate-600">
                      对应目标价 {formatPrice(derivedTargetPrice)}
                    </div>
                  )}
                </div>
              )}

              {triggerMode !== 'PROFIT' && (
                <div className="grid gap-1.5">
                  <Label
                    htmlFor="take-profit-target-price"
                    className="text-ui-caption font-black text-slate-500"
                  >
                    目标价
                  </Label>
                  <Input
                    id="take-profit-target-price"
                    type="number"
                    inputMode="decimal"
                    value={targetPrice}
                    onChange={event => setTargetPrice(event.target.value)}
                    className="h-9 rounded-md border-white/10 bg-[#050b14] text-ui-label font-bold text-slate-100"
                  />
                  {derivedTargetProfitPct !== null && (
                    <div className="text-ui-caption font-bold text-slate-600">
                      对应收益率{' '}
                      {formatPercentOrDash(derivedTargetProfitPct, true)}
                    </div>
                  )}
                </div>
              )}
            </div>

            {triggerMode === 'EITHER' && (
              <div className="rounded-md border border-amber-400/20 bg-amber-500/10 px-3 py-2 text-ui-caption font-bold text-amber-100">
                任一条件满足即触发。历史双条件订单按当前后端 OR 语义执行。
              </div>
            )}
          </div>

          <div className="space-y-3 rounded-md border border-white/5 bg-[#08101d]/75 p-3">
            <SectionTitle icon={SlidersHorizontal} label="卖出计划" />
            <div className="grid grid-cols-3 gap-2">
              <button
                type="button"
                onClick={() => applyPreset('10', 'PERCENT_AVAILABLE', '30')}
                className="rounded-md border border-white/10 bg-white/[0.025] px-2 py-2 text-left text-ui-caption font-bold text-slate-300 transition-colors hover:border-market-down/25 hover:bg-market-down/10"
              >
                <div className="font-black text-slate-100">轻仓止盈</div>
                +10% / 卖30%
              </button>
              <button
                type="button"
                onClick={() => applyPreset('15', 'PERCENT_AVAILABLE', '50')}
                className="rounded-md border border-white/10 bg-white/[0.025] px-2 py-2 text-left text-ui-caption font-bold text-slate-300 transition-colors hover:border-market-down/25 hover:bg-market-down/10"
              >
                <div className="font-black text-slate-100">标准止盈</div>
                +15% / 卖50%
              </button>
              <button
                type="button"
                onClick={() => applyPreset('20', 'ALL_AVAILABLE')}
                className="rounded-md border border-white/10 bg-white/[0.025] px-2 py-2 text-left text-ui-caption font-bold text-slate-300 transition-colors hover:border-market-down/25 hover:bg-market-down/10"
              >
                <div className="font-black text-slate-100">全部止盈</div>
                +20% / 全部
              </button>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div className="grid gap-1.5">
                <Label
                  htmlFor="take-profit-sell-mode"
                  className="text-ui-caption font-black text-slate-500"
                >
                  卖出数量
                </Label>
                <Select
                  value={sellMode}
                  onValueChange={value =>
                    setSellMode(value as TakeProfitSellMode)
                  }
                >
                  <SelectTrigger
                    id="take-profit-sell-mode"
                    className="h-control-default rounded-md border-white/10 bg-[#050b14] text-ui-label font-bold text-slate-100"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="ALL_AVAILABLE">全部可卖</SelectItem>
                    <SelectItem value="PERCENT_AVAILABLE">按比例</SelectItem>
                    <SelectItem value="FIXED_VOLUME">固定股数</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="grid gap-1.5">
                <Label
                  htmlFor="take-profit-sell-value"
                  className="text-ui-caption font-black text-slate-500"
                >
                  {sellMode === 'PERCENT_AVAILABLE'
                    ? '卖出比例 (%)'
                    : sellMode === 'FIXED_VOLUME'
                      ? '卖出股数'
                      : '可卖库存'}
                </Label>
                <Input
                  id="take-profit-sell-value"
                  type="number"
                  inputMode="decimal"
                  value={
                    sellMode === 'PERCENT_AVAILABLE'
                      ? sellRatioPct
                      : sellMode === 'FIXED_VOLUME'
                        ? sellVolume
                        : String(sellableVolume)
                  }
                  disabled={sellMode === 'ALL_AVAILABLE'}
                  onChange={event => {
                    if (sellMode === 'PERCENT_AVAILABLE') {
                      setSellRatioPct(event.target.value);
                    } else {
                      setSellVolume(event.target.value);
                    }
                  }}
                  className="h-9 rounded-md border-white/10 bg-[#050b14] text-ui-label font-bold text-slate-100 disabled:opacity-70"
                />
              </div>
            </div>
          </div>
        </div>

        {strategyId === 'PARTIAL_TRAILING' && (
          <div className="grid gap-3 rounded-md border border-market-down/15 bg-market-down/[0.06] p-3 md:grid-cols-2">
            <div className="grid gap-1.5">
              <Label
                htmlFor="take-profit-execution-mode"
                className="text-ui-caption font-black text-slate-500"
              >
                自动止盈执行模式
              </Label>
              <Select value={executionMode} onValueChange={setExecutionMode}>
                <SelectTrigger
                  id="take-profit-execution-mode"
                  className="h-control-default rounded-md border-white/10 bg-[#050b14] text-ui-label font-bold text-slate-100"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="paper">模拟执行（paper）</SelectItem>
                  <SelectItem value="live">实盘执行（live）</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <label className="flex cursor-pointer items-center gap-2 rounded-md border border-white/10 bg-[#050b14] px-3 text-ui-caption font-bold text-slate-300">
              <input
                type="checkbox"
                checked={autoExitAuthorized}
                onChange={event => setAutoExitAuthorized(event.target.checked)}
                className="h-3.5 w-3.5 accent-market-down"
              />
              明确授权达到动态退出条件后自动提交卖单
            </label>
          </div>
        )}

        <div
          className="grid gap-3 rounded-md border border-blue-400/15 bg-blue-500/10 p-3 xl:grid-cols-4"
          data-testid="take-profit-execution-preview"
        >
          <div className="xl:col-span-2">
            <SectionTitle icon={Activity} label="执行预览" />
            <div className="mt-2 text-ui-label font-bold leading-5 text-blue-100">
              {preview.triggerSummary} 后提交 SELL 委托，成交以券商回报为准。
            </div>
            {strategyId === 'PARTIAL_TRAILING' && (
              <div className="mt-1 text-ui-caption font-bold text-blue-200/70">
                达标后不立即卖出：强势放量继续跟涨，量价转弱连续确认或快速回撤时卖出固定保护股数。
              </div>
            )}
          </div>
          <PlanMetric
            label="计划卖出"
            value={`${preview.estimatedSellVolume.toLocaleString()} 股`}
          />
          <PlanMetric
            label="估算委托市值"
            value={formatCurrency(preview.estimatedOrderValue)}
          />
          <div className="xl:col-span-4 rounded-md border border-white/5 bg-[#050b14]/60 px-3 py-2 text-ui-caption font-bold leading-5 text-slate-500">
            T+1、可卖量、冻结、停牌、涨跌停、100
            股整数倍与零股清仓仍由交易域和券商回报校验。
          </div>
          {order?.strategy === 'ADAPTIVE_VOLUME_PRICE_TRAILING' && (
            <div className="grid gap-2 xl:col-span-4 md:grid-cols-4">
              <PlanMetric label="动态阶段" value={order.phase || '--'} />
              <PlanMetric
                label="剩余保护"
                value={`${order.remainingVolume ?? order.sellVolume ?? 0} 股`}
              />
              <PlanMetric
                label="峰值回撤"
                value={formatPercentOrDash(order.peakDrawdownPct)}
              />
              <PlanMetric
                label="量价评分 / 数据"
                value={`${order.weakScore ?? 0} / ${order.dataQuality || '--'}`}
              />
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-white/5 bg-[#08101d]/75 px-3 py-2">
          {!isSaveSupported ? (
            <div
              className="text-ui-caption font-bold text-amber-100"
              role="alert"
            >
              该策略待接入监控引擎，暂不能保存为自动执行计划。
            </div>
          ) : (
            <div className="inline-flex items-center gap-2 text-ui-caption font-bold text-slate-500">
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-300" />
              可保存为真实止盈监控单
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {order?.id && !isTerminal && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                title="取消止盈计划"
                disabled={actionLoading}
                onClick={() => order.id && onCancel(order.id)}
                className="h-8 px-2 text-ui-caption font-black text-rose-200 hover:text-rose-100"
              >
                <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                取消计划
              </Button>
            )}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={!canOperate || isLoading || !isSaveSupported}
              onClick={() => handleSave(false)}
              className="h-8 px-3 text-ui-caption font-black"
            >
              <Clock className="mr-1.5 h-3.5 w-3.5" />
              仅保存
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={!canOperate || isLoading || !isSaveSupported}
              onClick={() => handleSave(true)}
              className="h-8 bg-market-down px-ui-section text-ui-caption font-black text-white hover:bg-market-down/90"
            >
              {isLoading ? (
                <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="mr-2 h-3.5 w-3.5" />
              )}
              保存并启用止盈计划
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}
