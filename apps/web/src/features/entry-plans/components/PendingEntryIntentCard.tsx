import { Clock3, ShieldAlert, X } from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';

import { formatEntryCurrency, formatEntryDateTime } from '../model/draft';
import type {
  EntryPlanController,
  PendingEntryIntentView,
} from '../model/types';

const strategyLabels = {
  MANUAL_TRIGGER: '人工触发',
  PRICE_LADDER: '价格阶梯',
  TREND_PULLBACK_CONFIRMATION: '趋势回撤',
} as const;

export function PendingEntryIntentCard({
  controller,
  intent,
  now = Date.now(),
}: {
  controller: EntryPlanController;
  intent: PendingEntryIntentView;
  now?: number;
}) {
  const expiresAt = new Date(intent.expiresAt).getTime();
  const expired = !Number.isFinite(expiresAt) || expiresAt <= now;
  const deviation =
    intent.referencePrice > 0
      ? ((intent.currentAskPrice - intent.referencePrice) /
          intent.referencePrice) *
        100
      : 0;
  const [actionBusy, setActionBusy] = React.useState(false);
  const [actionError, setActionError] = React.useState<string | null>(null);

  async function runAction(action: () => Promise<void>) {
    setActionBusy(true);
    setActionError(null);
    try {
      await action();
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : '买入意图操作失败'
      );
    } finally {
      setActionBusy(false);
    }
  }

  return (
    <article className="rounded-lg border border-amber-400/20 bg-amber-400/[0.04] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-black text-slate-100">
            {intent.instrumentName}
          </h3>
          <p className="mt-1 font-mono text-[11px] text-slate-500">
            {intent.instrumentCode} ·{' '}
            {intent.bucket === 'core' ? '核心仓' : '活跃仓'} ·{' '}
            {strategyLabels[intent.strategy]}
          </p>
        </div>
        <span className="inline-flex items-center gap-1 rounded border border-amber-400/25 bg-amber-400/10 px-2 py-1 text-[11px] font-bold text-amber-100">
          <Clock3 aria-hidden="true" className="h-3.5 w-3.5" />
          {expired
            ? '确认已过期'
            : `有效至 ${formatEntryDateTime(intent.expiresAt)}`}
        </span>
      </div>

      <dl className="mt-4 grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
        {[
          [
            '参考价 / 当前卖一',
            `¥${intent.referencePrice.toFixed(2)} / ¥${intent.currentAskPrice.toFixed(2)}`,
          ],
          ['价格偏离', `${deviation >= 0 ? '+' : ''}${deviation.toFixed(2)}%`],
          [
            '期望金额 / 候选数量',
            `${formatEntryCurrency(intent.expectedAmountCny)} / ${intent.candidateVolume} 股`,
          ],
          ['最新风控', intent.riskAction],
          ['计划累计成交', formatEntryCurrency(intent.planFilledAmountCny)],
          ['今日累计成交', formatEntryCurrency(intent.dailyFilledAmountCny)],
          [
            '现金缓冲',
            intent.cashBufferPct >= 0
              ? `${intent.cashBufferPct.toFixed(1)}%`
              : '确认时实时复核',
          ],
          ['信号时间', formatEntryDateTime(intent.signalAt)],
        ].map(([label, value]) => (
          <div
            className="rounded-md border border-white/5 bg-[#080d18]/70 p-2.5"
            key={label}
          >
            <dt className="text-[10px] font-bold text-slate-500">{label}</dt>
            <dd className="mt-1 font-mono font-bold text-slate-200">{value}</dd>
          </div>
        ))}
      </dl>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-white/5 pt-3">
        <p className="inline-flex items-center gap-1.5 text-[11px] text-slate-400">
          <ShieldAlert
            aria-hidden="true"
            className="h-3.5 w-3.5 text-amber-200"
          />
          确认时会重新获取价格、数量、计划版本和实时风控；变化后需再次确认。
        </p>
        <div className="flex gap-2">
          <Button
            disabled={actionBusy}
            size="sm"
            type="button"
            variant="outline"
            onClick={() =>
              void runAction(() => controller.rejectPendingIntent(intent.id))
            }
          >
            <X />
            拒绝
          </Button>
          <Button
            disabled={actionBusy || expired}
            size="sm"
            type="button"
            onClick={() =>
              void runAction(() => controller.previewPendingIntent(intent.id))
            }
          >
            确认并重新风控
          </Button>
        </div>
      </div>
      {actionError ? (
        <p className="mt-2 text-[11px] text-rose-300" role="alert">
          {actionError}
        </p>
      ) : null}
    </article>
  );
}
