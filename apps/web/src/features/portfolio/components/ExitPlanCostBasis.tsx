import { Loader2, RefreshCw } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { cn } from '@/utils/cn';

import {
  costBasisModeLabel,
  readCostBasis,
  summarizeSelectedCostBasis,
  type ExitPlanCostBasisCandidate,
  type ExitPlanHoldingCapacity,
  type ManualCostBasisMode,
} from './exitPlanCostBasisUtils';

function formatDateTime(value?: string | null) {
  if (!value) return '--';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString('zh-CN');
}

export function ExitPlanCostBasisSummary({
  editableNotice = false,
  showFrozenAt = false,
  value,
}: {
  editableNotice?: boolean;
  showFrozenAt?: boolean;
  value: unknown;
}) {
  const basis = readCostBasis(value);
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] font-bold text-slate-400">
      <span>成本依据：{costBasisModeLabel(basis.mode)}</span>
      <span>冻结成本 ¥{basis.unitCost.toFixed(4)}/股</span>
      {basis.basisVolume > 0 && (
        <span>依据数量 {basis.basisVolume.toLocaleString()} 股</span>
      )}
      {showFrozenAt && basis.frozenAt && (
        <span>冻结于 {formatDateTime(basis.frozenAt)}</span>
      )}
      {editableNotice && (
        <span className="text-slate-500">
          成本依据创建后冻结；如需更换，请取消并重建计划。
        </span>
      )}
    </div>
  );
}

export function ExitPlanCapacityBanner({
  busy,
  capacity,
  onReconcile,
}: {
  busy: boolean;
  capacity?: ExitPlanHoldingCapacity | null;
  onReconcile: () => void;
}) {
  if (!capacity) return null;
  const requiresReconciliation =
    capacity.capacityStatus === 'RECONCILE_REQUIRED';
  return (
    <div
      className={cn(
        'mt-3 flex flex-wrap items-center justify-between gap-2 rounded border px-3 py-2 text-[11px] font-bold',
        requiresReconciliation
          ? 'border-amber-400/30 bg-amber-500/10 text-amber-100'
          : 'border-white/8 bg-black/10 text-slate-400'
      )}
      role={requiresReconciliation ? 'alert' : undefined}
    >
      <span>
        持仓 {capacity.totalVolume} · 已纳入计划 {capacity.protectedVolume} ·
        可加入计划 {capacity.unallocatedVolume} 股
        {capacity.capacityError ? ` · ${capacity.capacityError}` : ''}
      </span>
      {requiresReconciliation && (
        <Button
          disabled={busy}
          onClick={onReconcile}
          size="sm"
          type="button"
          variant="outline"
        >
          {busy ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          按最新持仓重新对账
        </Button>
      )}
    </div>
  );
}

export function ExitPlanCostBasisEditor({
  candidates,
  candidatesFetching,
  editingCostBasis,
  historyWarning,
  manualUnitCost,
  mode,
  onManualUnitCostChange,
  onModeChange,
  onSelectedOrderIdsChange,
  requestedVolume,
  selectedOrderIds,
}: {
  candidates: readonly ExitPlanCostBasisCandidate[];
  candidatesFetching: boolean;
  editingCostBasis?: unknown;
  historyWarning?: string | null;
  manualUnitCost: string;
  mode: ManualCostBasisMode;
  onManualUnitCostChange: (value: string) => void;
  onModeChange: (mode: ManualCostBasisMode) => void;
  onSelectedOrderIdsChange: (value: string[]) => void;
  requestedVolume: number;
  selectedOrderIds: string[];
}) {
  const selected = summarizeSelectedCostBasis(candidates, selectedOrderIds);
  return (
    <fieldset className="mt-3 rounded-md border border-white/8 bg-black/10 p-3">
      <legend className="px-1 text-xs font-black text-slate-200">
        成本依据
      </legend>
      {editingCostBasis !== undefined ? (
        <ExitPlanCostBasisSummary editableNotice value={editingCostBasis} />
      ) : (
        <div className="grid gap-3">
          <div className="grid gap-2 sm:grid-cols-2">
            <label
              className={cn(
                'cursor-pointer rounded-md border p-3 transition-colors',
                mode === 'BROKER_BUY_ORDERS'
                  ? 'border-blue-400/50 bg-blue-500/10'
                  : 'border-white/8 hover:border-white/20'
              )}
            >
              <span className="flex items-center gap-2 text-xs font-black text-slate-200">
                <input
                  checked={mode === 'BROKER_BUY_ORDERS'}
                  name="manual-plan-cost-basis"
                  onChange={() => onModeChange('BROKER_BUY_ORDERS')}
                  type="radio"
                />
                使用已成交买入委托
              </span>
              <span className="mt-1 block text-[10px] font-medium leading-4 text-slate-500">
                服务端按所选成交额和估算买入费用计算，并在创建时冻结。
              </span>
            </label>
            <label
              className={cn(
                'cursor-pointer rounded-md border p-3 transition-colors',
                mode === 'MANUAL_UNIT_COST'
                  ? 'border-blue-400/50 bg-blue-500/10'
                  : 'border-white/8 hover:border-white/20'
              )}
            >
              <span className="flex items-center gap-2 text-xs font-black text-slate-200">
                <input
                  checked={mode === 'MANUAL_UNIT_COST'}
                  name="manual-plan-cost-basis"
                  onChange={() => onModeChange('MANUAL_UNIT_COST')}
                  type="radio"
                />
                手工填写每股全成本
              </span>
              <span className="mt-1 block text-[10px] font-medium leading-4 text-slate-500">
                适合历史委托不完整；输入值需已包含买入手续费。
              </span>
            </label>
          </div>
          {mode === 'BROKER_BUY_ORDERS' ? (
            <div className="grid gap-2">
              <div className="max-h-48 overflow-y-auto rounded-md border border-white/8">
                {candidatesFetching && candidates.length === 0 ? (
                  <div className="flex items-center gap-2 p-3 text-xs text-slate-500">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    正在读取已成交买入委托
                  </div>
                ) : candidates.length === 0 ? (
                  <div className="p-3 text-xs font-bold text-slate-500">
                    没有可用的买入成交记录，请改用手工每股全成本。
                  </div>
                ) : (
                  candidates.map(item => (
                    <label
                      className="flex cursor-pointer items-start gap-3 border-b border-white/5 p-3 last:border-b-0 hover:bg-white/[0.03]"
                      key={item.orderId}
                    >
                      <input
                        checked={selectedOrderIds.includes(item.orderId)}
                        className="mt-0.5"
                        onChange={event =>
                          onSelectedOrderIdsChange(
                            event.target.checked
                              ? [...selectedOrderIds, item.orderId]
                              : selectedOrderIds.filter(
                                  id => id !== item.orderId
                                )
                          )
                        }
                        type="checkbox"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap justify-between gap-2 text-xs font-black text-slate-200">
                          <span>委托 #{item.orderId}</span>
                          <span>
                            {item.tradedVolume.toLocaleString()} 股 × ¥
                            {item.tradedPrice.toFixed(3)}
                          </span>
                        </span>
                        <span className="mt-1 block text-[10px] font-medium text-slate-500">
                          {formatDateTime(item.orderTime)} · 估算买入费 ¥
                          {item.estimatedBuyFeeCny.toFixed(2)}
                        </span>
                      </span>
                    </label>
                  ))
                )}
              </div>
              <div
                className={cn(
                  'rounded border px-3 py-2 text-[11px] font-bold',
                  selected.volume >= requestedVolume && selected.volume > 0
                    ? 'border-emerald-400/20 text-emerald-200'
                    : 'border-amber-400/25 text-amber-200'
                )}
                role="status"
              >
                已选 {selected.count} 笔 · 成交{' '}
                {selected.volume.toLocaleString()} 股 · 冻结成本约 ¥
                {selected.unitCost.toFixed(4)}/股
                {selected.volume < requestedVolume && (
                  <span>
                    {' '}
                    · 尚差{' '}
                    {Math.max(
                      0,
                      requestedVolume - selected.volume
                    ).toLocaleString()}{' '}
                    股
                  </span>
                )}
              </div>
              <p className="text-[10px] font-medium leading-4 text-slate-500">
                {historyWarning ||
                  '计划创建后成本依据冻结，后续买入不会改变该计划。'}{' '}
                整笔买入委托只能作为一个有效卖出计划的成本依据。
              </p>
            </div>
          ) : (
            <div className="grid gap-1 text-xs font-bold text-slate-400">
              <label htmlFor="manual-plan-unit-cost">每股全成本（元）</label>
              <input
                aria-describedby="manual-plan-unit-cost-help"
                className="h-9 rounded-md border border-white/10 bg-[#080d18] px-3 font-mono text-slate-100 outline-none focus:border-blue-400/50"
                id="manual-plan-unit-cost"
                min="0.0001"
                onChange={event => onManualUnitCostChange(event.target.value)}
                placeholder="例如 42.8365"
                step="0.0001"
                type="number"
                value={manualUnitCost}
              />
              <span
                className="text-[10px] font-medium leading-4 text-slate-500"
                id="manual-plan-unit-cost-help"
              >
                必须包含买入手续费；系统只会另外估算卖出费用。
              </span>
            </div>
          )}
        </div>
      )}
    </fieldset>
  );
}
