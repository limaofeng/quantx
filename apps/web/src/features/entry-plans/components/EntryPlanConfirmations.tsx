import { ShieldAlert, ShieldCheck } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

import type {
  EntryAuthorizationChallenge,
  EntryIntentConfirmationPreview,
} from '../hooks/useEntryPlansWorkspace';
import { formatEntryCurrency, formatEntryDateTime } from '../model/draft';

const riskEnvelopeFields = [
  ['instrument_code', '固定标的'],
  ['bucket', '归因仓'],
  ['config_version', '配置版本'],
  ['max_total_amount_cny', '累计预算上限'],
  ['max_single_amount_cny', '单笔金额上限'],
  ['max_daily_amount_cny', '单日成交上限'],
  ['cash_buffer_pct', '最低现金缓冲'],
  ['max_position_pct', '绝对仓位上限'],
  ['max_buy_price', '最高可买价'],
  ['max_slippage_bps', '最大滑点'],
  ['max_price_deviation_bps', '最大价格偏离'],
  ['account_snapshot_version', '账户快照版本'],
] as const;

function formatRiskEnvelopeValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '--';
  if (key.includes('amount_cny')) return formatEntryCurrency(Number(value));
  if (key === 'max_position_pct' || key === 'cash_buffer_pct') {
    return `${(Number(value) * 100).toFixed(1)}%`;
  }
  if (key === 'max_buy_price') return `¥${Number(value).toFixed(3)}`;
  if (key.endsWith('_bps')) return `${Number(value).toFixed(0)} bps`;
  if (key === 'bucket') return value === 'swing' ? '活跃仓' : '核心仓';
  return String(value);
}

export function EntryAuthorizationConfirmationDialog({
  challenge,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  challenge: EntryAuthorizationChallenge | null;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}) {
  const challengeExpired = challenge
    ? new Date(challenge.challengeExpiresAt).getTime() <= Date.now()
    : false;
  return (
    <Dialog
      open={challenge !== null}
      onOpenChange={open => {
        if (!open) onCancel();
      }}
    >
      <DialogContent className="max-h-[90vh] overflow-y-auto border-amber-400/25 bg-[#0b1120] text-slate-100 sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <ShieldAlert
              aria-hidden="true"
              className="h-5 w-5 text-amber-300"
            />
            确认实盘自动建仓授权
          </DialogTitle>
          <DialogDescription className="text-left leading-5 text-slate-400">
            这是绑定本机设备、计划版本与风险边界的限时授权。只有点击下方确认后，LIVE
            AUTO 计划才会开始监控和自动路由买单。
          </DialogDescription>
        </DialogHeader>
        {challenge ? (
          <div className="space-y-3">
            <p className="rounded-md border border-amber-400/20 bg-amber-400/[0.08] p-3 text-sm leading-6 text-amber-50">
              {challenge.summary}
            </p>
            <dl className="grid gap-2 text-xs sm:grid-cols-2">
              {riskEnvelopeFields.map(([key, label]) => (
                <div
                  className="rounded-md border border-white/10 bg-white/[0.025] p-2.5"
                  key={key}
                >
                  <dt className="text-slate-500">{label}</dt>
                  <dd className="mt-1 break-words font-mono font-bold text-slate-200">
                    {formatRiskEnvelopeValue(key, challenge.riskEnvelope[key])}
                  </dd>
                </div>
              ))}
            </dl>
            <div className="rounded-md border border-white/10 p-3 text-[11px] leading-5 text-slate-400">
              <p>
                挑战有效至：{formatEntryDateTime(challenge.challengeExpiresAt)}
              </p>
              <p>
                授权有效至：
                {formatEntryDateTime(challenge.authorizationExpiresAt)}
              </p>
              <p className="break-all font-mono text-slate-500">
                授权指纹：{challenge.authorizationFingerprint}
              </p>
            </div>
            <p className="text-[11px] leading-5 text-slate-500">
              确认不代表成交。每笔订单仍受账户资金、仓位、交易时段、涨跌停、整手和实时价格风控约束；只有券商成交回报会累计真实已买金额。
            </p>
            {challengeExpired ? (
              <p className="text-xs text-rose-300" role="alert">
                本次授权挑战已过期，请关闭后重新请求预览。
              </p>
            ) : null}
          </div>
        ) : null}
        {error ? (
          <p className="text-xs text-rose-300" role="alert">
            {error}
          </p>
        ) : null}
        <DialogFooter>
          <Button
            disabled={busy}
            type="button"
            variant="outline"
            onClick={onCancel}
          >
            暂不授权
          </Button>
          <Button
            disabled={busy || !challenge || challengeExpired}
            type="button"
            onClick={onConfirm}
          >
            <ShieldCheck />
            {busy ? '正在确认…' : '确认授权并启动'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function EntryIntentConfirmationDialog({
  preview,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  preview: EntryIntentConfirmationPreview | null;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}) {
  const challengeExpired = preview
    ? new Date(preview.challengeExpiresAt).getTime() <= Date.now()
    : false;
  const intentExpired = preview
    ? preview.expiresAtMs > 0 && preview.expiresAtMs <= Date.now()
    : false;
  return (
    <Dialog
      open={preview !== null}
      onOpenChange={open => {
        if (!open) onCancel();
      }}
    >
      <DialogContent className="border-cyan-400/20 bg-[#0b1120] text-slate-100 sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-base">确认本次买入</DialogTitle>
          <DialogDescription className="text-left leading-5 text-slate-400">
            以下数量与价格来自刚刚执行的最新行情、计划版本、资金和 A
            股订单风控。
          </DialogDescription>
        </DialogHeader>
        {preview ? (
          <div className="space-y-3">
            <dl className="grid grid-cols-2 gap-2 text-xs">
              {[
                ['固定标的', preview.instrumentCode],
                [
                  '信号价 / 最新价',
                  `¥${preview.signalPrice.toFixed(2)} / ¥${preview.latestPrice.toFixed(2)}`,
                ],
                [
                  '最新价格偏离',
                  `${(preview.priceDeviationBps / 100).toFixed(2)}%`,
                ],
                ['请求金额', formatEntryCurrency(preview.requestedAmountCny)],
                ['规范化数量', `${preview.sizedVolume} 股`],
                ['风控后最终数量', `${preview.finalVolume} 股`],
                ['风控结论', preview.riskAction],
                [
                  '确认有效至',
                  formatEntryDateTime(
                    preview.expiresAtMs > 0
                      ? new Date(preview.expiresAtMs).toISOString()
                      : null
                  ),
                ],
                [
                  '设备挑战有效至',
                  formatEntryDateTime(preview.challengeExpiresAt),
                ],
              ].map(([label, value]) => (
                <div
                  className="rounded-md border border-white/10 bg-white/[0.025] p-2.5"
                  key={label}
                >
                  <dt className="text-slate-500">{label}</dt>
                  <dd className="mt-1 font-mono font-bold text-slate-200">
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
            <p
              className={
                preview.valid
                  ? 'rounded-md border border-emerald-400/20 bg-emerald-400/[0.07] p-3 text-xs text-emerald-100'
                  : 'rounded-md border border-rose-400/20 bg-rose-400/[0.07] p-3 text-xs text-rose-100'
              }
              role="status"
            >
              {preview.message ||
                (preview.valid ? '最新风控允许确认' : '最新风控不允许买入')}
            </p>
            {preview.warnings.length > 0 ? (
              <ul
                aria-label="本次买入确认警告"
                className="space-y-1 rounded-md border border-amber-400/20 bg-amber-400/[0.06] p-3 text-[11px] leading-5 text-amber-100"
              >
                {preview.warnings.map(warning => (
                  <li key={warning}>• {warning}</li>
                ))}
              </ul>
            ) : null}
            <p className="text-[11px] leading-5 text-slate-500">
              本次确认令牌只绑定当前设备、账户、计划和意图，且只能使用一次。确认成功只会进入统一实时复核和下单链路，券商成交回报才是成交事实。
            </p>
            {challengeExpired || intentExpired ? (
              <p className="text-xs text-rose-300" role="alert">
                {intentExpired
                  ? '原买入意图已过期，请等待计划重新评估。'
                  : '本次设备确认挑战已过期，请重新获取最新风控预览。'}
              </p>
            ) : null}
          </div>
        ) : null}
        {error ? (
          <p className="text-xs text-rose-300" role="alert">
            {error}
          </p>
        ) : null}
        <DialogFooter>
          <Button
            disabled={busy}
            type="button"
            variant="outline"
            onClick={onCancel}
          >
            返回
          </Button>
          <Button
            disabled={
              busy || !preview?.valid || challengeExpired || intentExpired
            }
            type="button"
            onClick={onConfirm}
          >
            {busy ? '正在提交…' : '确认提交买入'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
