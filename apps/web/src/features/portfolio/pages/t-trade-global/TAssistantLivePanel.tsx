import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery } from 'urql';

import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Portfolio_ConfirmTAssistantLiveEntryDocument,
  Portfolio_PreviewTAssistantLiveEntryDocument,
  Portfolio_TAssistantLiveApprovalQueueDocument,
  type Portfolio_PreviewTAssistantLiveEntryMutation,
} from '@/generated/t-assistant-live/graphql';

type Preview = NonNullable<
  Portfolio_PreviewTAssistantLiveEntryMutation['previewTAssistantLiveEntry']['preview']
>;
const labels: Readonly<Record<string, string>> = {
  WARMING: '预热中',
  RUNNING: '运行中',
  DRAINING: '停止接收买入',
  RECONCILE_REQUIRED: '待核对',
  READY: '就绪',
  BLOCKED: '已阻断',
  MANUAL_CONFIRM: '人工确认',
  AUTO: '自动',
  AWAITING_APPROVAL: '等待确认',
  ALLOCATION_PENDING: '等待重新分配',
  EXECUTION_READY: '执行准备就绪',
  PENDING: '确认处理中',
  SUCCEEDED: '确认已处理',
  FAILED: '确认未执行',
  UNKNOWN: '确认结果待核对',
};
const time = (value?: string | null) =>
  value
    ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
    : '未记录';
const money = (value?: number | null) =>
  value == null
    ? '未记录'
    : value.toLocaleString('zh-CN', { style: 'currency', currency: 'CNY' });

export function TAssistantLivePanel({ accountId }: { accountId: string }) {
  return <LiveApprovalWorkspace key={accountId} accountId={accountId} />;
}

function LiveApprovalWorkspace({ accountId }: { accountId: string }) {
  const [query, refresh] = useQuery({
    query: Portfolio_TAssistantLiveApprovalQueueDocument,
    variables: { accountId },
    pause: !accountId,
    requestPolicy: 'network-only',
  });
  const [issueState, issue] = useMutation(
    Portfolio_PreviewTAssistantLiveEntryDocument
  );
  const [confirmState, confirm] = useMutation(
    Portfolio_ConfirmTAssistantLiveEntryDocument
  );
  const [preview, setPreview] = useState<Preview | null>(null);
  const [message, setMessage] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const [queued, setQueued] = useState<string | null>(null);
  const inFlight = useRef(false);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const interval = window.setInterval(
      () => refresh({ requestPolicy: 'network-only' }),
      5000
    );
    return () => window.clearInterval(interval);
  }, [refresh]);
  useEffect(() => {
    if (!preview) return;
    const interval = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, [preview]);
  const current =
    query.operation?.variables.accountId === accountId && !query.error;
  const queue = current ? query.data?.tAssistantLiveApprovalQueue : undefined;
  const busy = issueState.fetching || confirmState.fetching;
  const expired =
    preview != null &&
    (!Number.isFinite(Date.parse(preview.challengeExpiresAt)) ||
      now >= Date.parse(preview.challengeExpiresAt));

  async function openPreview(intentId: string) {
    if (!queue?.executionId || busy || uncertain || inFlight.current) return;
    inFlight.current = true;
    try {
      setMessage('');
      setPreview(null);
      const result = await issue({
        accountId,
        executionId: queue.executionId,
        intentId,
      });
      const value = result.data?.previewTAssistantLiveEntry;
      if (result.error || !value?.success || !value.preview) {
        setMessage(value?.message || '预览暂不可用，请刷新后重试');
        return;
      }
      const candidate = value.preview;
      if (
        candidate.accountId !== accountId ||
        candidate.intentId !== intentId ||
        candidate.executionOwner.ownerId !== queue.executionId ||
        candidate.executionOwner.ownerType !== 'T_ASSISTANT_EXECUTION' ||
        candidate.environment !== 'LIVE' ||
        !candidate.tTradeAutoExitAuthorization
      ) {
        setMessage('确认材料与当前执行不一致，请刷新后重试');
        return;
      }
      setNow(Date.now());
      setUncertain(false);
      setPreview(candidate);
    } catch {
      setMessage('预览暂不可用，请刷新后重试');
    } finally {
      inFlight.current = false;
    }
  }

  async function submit() {
    if (!preview || busy || inFlight.current || (expired && !uncertain)) return;
    inFlight.current = true;
    try {
      const original = preview;
      const result = await confirm({
        accountId,
        executionId: original.executionOwner.ownerId,
        intentId: original.intentId,
        confirmationToken: original.confirmationToken,
      });
      const value = result.data?.confirmTAssistantLiveEntry;
      const unknown =
        Boolean(result.error) ||
        !value ||
        value.code === 'T_ASSISTANT_CONFIRMATION_OUTCOME_UNKNOWN';
      setUncertain(unknown);
      setMessage(
        unknown
          ? '确认结果暂未明确，请重试原确认请求'
          : value?.message || '请刷新确认状态'
      );
      if (value?.success && !result.error) {
        setQueued(original.intentId);
        setPreview(null);
      } else if (!unknown) {
        setPreview(null);
      }
      refresh({ requestPolicy: 'network-only' });
    } catch {
      setUncertain(true);
      setMessage('确认结果暂未明确，请重试原确认请求');
    } finally {
      inFlight.current = false;
    }
  }

  return (
    <section
      className="flex h-full min-h-0 flex-col gap-ui-section overflow-auto p-ui-section text-ui-body text-slate-200"
      aria-label="LIVE 人工确认"
    >
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-ui-title font-medium">独立 LIVE · 人工确认</h2>
          <p className="text-ui-caption text-slate-400">
            确认后重新计算额度并通过账户风控；提交确认不表示已下单或成交。
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => refresh({ requestPolicy: 'network-only' })}
          disabled={query.fetching}
        >
          刷新
        </Button>
      </div>
      {message && (
        <p role="status" className="text-ui-label text-amber-300">
          {message}
        </p>
      )}
      {query.error ? (
        <p role="alert">读取确认队列失败，请刷新重试。</p>
      ) : !queue ? (
        <p>正在读取执行状态…</p>
      ) : !queue.executionId ? (
        <p>尚无独立 LIVE 执行实例。</p>
      ) : (
        <>
          <div className="text-ui-label text-slate-400">
            {labels[queue.status || ''] || queue.status} ·{' '}
            {labels[queue.entryReadiness || ''] || queue.entryReadiness} ·{' '}
            {labels[queue.entryAuthorization || ''] || queue.entryAuthorization}
            {queue.reasonCodes.length > 0 && (
              <p>{queue.reasonCodes.join('、')}</p>
            )}
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>标的</TableHead>
                <TableHead>申请金额</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>有效期</TableHead>
                <TableHead>操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {queue.entries.map(entry => (
                <TableRow key={entry.intentId}>
                  <TableCell className="font-mono">
                    {entry.instrumentCode}
                    <div className="text-ui-caption text-slate-400">
                      {entry.reason}
                    </div>
                  </TableCell>
                  <TableCell className="font-mono">
                    {money(entry.requestedAmount)}
                  </TableCell>
                  <TableCell>
                    {entry.confirmationStatus === 'NONE' ||
                    entry.status !== 'AWAITING_APPROVAL'
                      ? labels[entry.status] || entry.status
                      : labels[entry.confirmationStatus] ||
                        entry.confirmationStatus}
                  </TableCell>
                  <TableCell className="font-mono text-ui-caption">
                    {time(entry.expiresAt)}
                  </TableCell>
                  <TableCell>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={
                        !entry.canPreview ||
                        busy ||
                        uncertain ||
                        query.fetching ||
                        (queued === entry.intentId &&
                          entry.confirmationStatus !== 'FAILED')
                      }
                      onClick={() => void openPreview(entry.intentId)}
                    >
                      核对并确认
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {!queue.entries.length && (
            <p className="text-slate-400">当前没有等待处理的买入意图。</p>
          )}
          {queue.truncated && (
            <p className="text-ui-caption text-slate-400">
              仅显示最近 50 条，请处理后刷新。
            </p>
          )}
        </>
      )}
      {preview && (
        <section
          className="rounded-md border border-blue-400/30 bg-slate-900/60 p-ui-section"
          aria-label="买入与自动退出确认预览"
        >
          <h3 className="text-ui-title font-medium">
            买入 {preview.instrumentCode}
          </h3>
          <p>
            参考价 {money(preview.referencePrice)} · 预计金额{' '}
            {money(preview.estimatedAmount)} · 数量{' '}
            {preview.targetVolume ?? '重新分配后确定'}
          </p>
          <p className="text-ui-caption text-slate-400">
            确认有效期：{time(preview.challengeExpiresAt)}
          </p>
          <p className="mt-2">
            自动退出保护上限：
            {preview.tTradeAutoExitAuthorization?.maxProtectedVolume} 股
          </p>
          <p>{preview.tTradeAutoExitAuthorization?.executionSemantics}</p>
          <p className="text-ui-caption text-slate-400">
            保护有效期：
            {time(preview.tTradeAutoExitAuthorization?.authorizationExpiresAt)}
          </p>
          <details className="my-2">
            <summary className="cursor-pointer text-ui-label">
              核对自动退出规则
            </summary>
            <pre className="overflow-auto whitespace-pre-wrap text-ui-caption">
              {JSON.stringify(
                preview.tTradeAutoExitAuthorization?.rules,
                null,
                2
              )}
            </pre>
          </details>
          {preview.warnings.map(warning => (
            <p key={warning} className="text-ui-label text-amber-300">
              {warning}
            </p>
          ))}
          {expired && !uncertain && (
            <p role="alert" className="text-amber-300">
              预览已过期，请重新获取。
            </p>
          )}
          <div className="mt-3 flex gap-2">
            <Button
              size="sm"
              onClick={() => void submit()}
              disabled={busy || (expired && !uncertain)}
            >
              {uncertain ? '重试原确认' : '确认买入及自动退出保护'}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy || uncertain}
              onClick={() => setPreview(null)}
            >
              关闭预览
            </Button>
          </div>
        </section>
      )}
    </section>
  );
}
