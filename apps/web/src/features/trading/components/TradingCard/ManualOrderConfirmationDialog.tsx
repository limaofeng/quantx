import { AlertTriangle, CheckCircle2, Clock3, ShieldCheck } from 'lucide-react';
import * as React from 'react';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { ManualOrderSide } from '@/generated/gql/graphql';
import { formatCurrency } from '@/shared/utils/format';
import { cn } from '@/utils/cn';

import type { ManualOrderPreviewTicket } from './hooks/useTradingSubmit';

interface ManualOrderConfirmationDialogProps {
  confirmationError: string;
  isConfirming: boolean;
  onConfirm: () => Promise<boolean>;
  onDismiss: () => void;
  preview: ManualOrderPreviewTicket | null;
}

function formatNumber(value: number) {
  return value.toLocaleString('zh-CN');
}

function formatPrice(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value)
    ? `¥${value.toFixed(3)}`
    : '--';
}

function formatTime(value: string) {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp)
    ? new Date(timestamp).toLocaleTimeString('zh-CN', { hour12: false })
    : '--';
}

function DetailRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-white/5 py-1.5 last:border-b-0">
      <dt className="shrink-0 text-ui-label text-slate-500">{label}</dt>
      <dd className="min-w-0 text-right font-mono text-ui-label font-semibold text-slate-200">
        {value}
      </dd>
    </div>
  );
}

export function ManualOrderConfirmationDialog({
  confirmationError,
  isConfirming,
  onConfirm,
  onDismiss,
  preview,
}: ManualOrderConfirmationDialogProps) {
  const [now, setNow] = React.useState(Date.now());

  React.useEffect(() => {
    if (!preview) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [preview]);

  if (!preview) return null;

  const expiresAt = Date.parse(preview.challengeExpiresAt);
  const remainingSeconds = Number.isFinite(expiresAt)
    ? Math.max(0, Math.ceil((expiresAt - now) / 1000))
    : 0;
  const expired = remainingSeconds <= 0;
  const isLive = preview.executionMode === 'LIVE';
  const isBuy = preview.side === ManualOrderSide.Buy;
  const wasCapped = preview.finalVolume !== preview.requestedVolume;
  const quotedPrice =
    preview.priceType === 'LIMIT' ? preview.limitPrice : preview.referencePrice;

  return (
    <AlertDialog
      open
      onOpenChange={open => {
        if (!open && !isConfirming) onDismiss();
      }}
    >
      <AlertDialogContent className="max-h-[90vh] overflow-hidden border-slate-700 bg-slate-950 sm:max-w-xl">
        <AlertDialogHeader>
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-primary" aria-hidden="true" />
            <AlertDialogTitle>核对服务器委托预览</AlertDialogTitle>
          </div>
          <AlertDialogDescription>
            这里尚未下单。确认后只代表命令进入执行队列，最终状态以 QMT Agent
            和券商回报为准。
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="min-h-0 space-y-3 overflow-y-auto pr-1">
          <div
            className={cn(
              'rounded-panel border p-3 text-ui-label',
              isLive
                ? 'border-amber-400/30 bg-amber-400/10 text-amber-200'
                : 'border-primary/25 bg-primary/10 text-blue-200'
            )}
            role="status"
          >
            <div className="flex items-start gap-2">
              {isLive ? (
                <AlertTriangle
                  className="mt-0.5 h-4 w-4 shrink-0"
                  aria-hidden="true"
                />
              ) : (
                <CheckCircle2
                  className="mt-0.5 h-4 w-4 shrink-0"
                  aria-hidden="true"
                />
              )}
              <div>
                <p className="font-semibold">
                  {isLive ? 'LIVE 实盘委托' : 'PAPER 模拟委托'}
                </p>
                <p className="mt-1 text-ui-caption opacity-80">
                  {isLive
                    ? '本次确认会明确进入实盘执行链，请逐项核对标的、方向、价格和最终数量。'
                    : '本次确认只进入模拟执行链，不会自动切换为实盘。'}
                </p>
              </div>
            </div>
          </div>

          {wasCapped && (
            <div
              className="rounded-panel border border-amber-400/30 bg-amber-400/10 p-3 text-ui-label text-amber-200"
              role="status"
            >
              风控已将请求的 {formatNumber(preview.requestedVolume)}{' '}
              股缩减为合法数量 {formatNumber(preview.finalVolume)}{' '}
              股；确认只会提交合法数量。
            </div>
          )}

          <section className="rounded-panel border border-white/10 bg-slate-900/60 p-3">
            <div className="mb-2 flex items-baseline justify-between gap-3">
              <h3 className="font-mono text-ui-title font-bold text-slate-100">
                {preview.instrumentCode}
              </h3>
              <span
                className={cn(
                  'text-ui-title font-bold',
                  isBuy ? 'text-market-up' : 'text-market-down'
                )}
              >
                {isBuy ? '买入' : '卖出'}
              </span>
            </div>
            <dl>
              <DetailRow
                label="报价方式"
                value={preview.priceType === 'BEST' ? '对手方最优' : '限价'}
              />
              <DetailRow
                label="请求数量"
                value={`${formatNumber(preview.requestedVolume)} 股`}
              />
              <DetailRow
                label="最终合法数量"
                value={`${formatNumber(preview.finalVolume)} 股`}
              />
              <DetailRow
                label={preview.priceType === 'LIMIT' ? '委托限价' : '参考价格'}
                value={formatPrice(quotedPrice)}
              />
              {preview.priceType === 'LIMIT' && (
                <DetailRow
                  label="行情参考价"
                  value={formatPrice(preview.referencePrice)}
                />
              )}
              <DetailRow
                label="预估金额"
                value={formatCurrency(preview.estimatedAmount)}
              />
              <DetailRow
                label="预估费用"
                value={
                  preview.estimatedFees == null
                    ? '--'
                    : formatCurrency(preview.estimatedFees)
                }
              />
              <DetailRow
                label="可用资金"
                value={formatCurrency(preview.availableCash)}
              />
              {preview.availableVolume != null && (
                <DetailRow
                  label="可卖数量"
                  value={`${formatNumber(preview.availableVolume)} 股`}
                />
              )}
              <DetailRow
                label="风控结果"
                value={preview.riskAction === 'CAP' ? '允许 · 已缩量' : '允许'}
              />
              <DetailRow label="风控原因" value={preview.riskReasonCode} />
              <DetailRow
                label="行情时间"
                value={formatTime(preview.quoteTimestamp)}
              />
              <DetailRow
                label="票据有效期"
                value={
                  <span
                    className={cn(
                      'inline-flex items-center gap-1',
                      expired ? 'text-rose-300' : 'text-amber-200'
                    )}
                  >
                    <Clock3 className="h-3.5 w-3.5" aria-hidden="true" />
                    {expired ? '已过期' : `${remainingSeconds} 秒`}
                  </span>
                }
              />
            </dl>
            {preview.riskReasonDetail && (
              <p className="mt-2 text-ui-caption leading-5 text-slate-400">
                {preview.riskReasonDetail}
              </p>
            )}
          </section>

          <section className="rounded-panel border border-white/10 bg-slate-900/40 p-3">
            <h3 className="text-ui-label font-semibold text-slate-200">
              确认前检查
            </h3>
            {preview.warnings.length ? (
              <ul className="mt-2 space-y-1.5 text-ui-caption text-slate-400">
                {preview.warnings.map((warning, index) => (
                  <li
                    key={`${index}-${warning}`}
                    className="flex items-start gap-2"
                  >
                    <AlertTriangle
                      className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-300"
                      aria-hidden="true"
                    />
                    <span>{warning}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-ui-caption text-slate-500">
                服务端未返回额外提示；确认时仍会重新校验行情、账户和风控快照。
              </p>
            )}
          </section>

          {confirmationError && (
            <div
              className="rounded-panel border border-rose-400/30 bg-rose-400/10 p-3 text-ui-label text-rose-200"
              role="alert"
            >
              {confirmationError}
            </div>
          )}
        </div>

        <AlertDialogFooter className="border-t border-white/10 pt-3">
          <AlertDialogCancel disabled={isConfirming}>取消</AlertDialogCancel>
          <AlertDialogAction
            disabled={expired || isConfirming}
            onClick={event => {
              event.preventDefault();
              void onConfirm();
            }}
            className={cn(
              'text-white',
              isBuy
                ? 'bg-market-buy-cta hover:bg-market-buy-cta/90'
                : 'bg-market-down hover:bg-market-down/90'
            )}
          >
            {isConfirming
              ? '正在确认...'
              : expired
                ? '预览已过期'
                : `确认${isLive ? '实盘' : '模拟'}${isBuy ? '买入' : '卖出'} ${formatNumber(preview.finalVolume)} 股`}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
