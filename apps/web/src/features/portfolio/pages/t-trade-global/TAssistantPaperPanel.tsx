import { Loader2, RefreshCw } from 'lucide-react';
import type { ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import { NativeSelect } from '@/components/ui/native-select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  useTAssistantPaper,
  type PaperFacts,
  type PaperSection,
} from '@/features/portfolio/hooks/useTAssistantPaper';
import { cn } from '@/utils/cn';

const sections: readonly (readonly [PaperSection, string])[] = [
  ['opportunities', '原冻结候选'],
  ['allocations', '组合分配'],
  ['reasons', '阻断与原因'],
  ['orders', '委托与成交'],
  ['exitPlans', '退出保护'],
];
const labels: Readonly<Record<string, string>> = {
  RUNNING: '运行中',
  DRAINING: '停止接收买入',
  STOPPED: '已停止',
  READY: '就绪',
  WARMING: '预热中',
  RECONCILE: '待核对',
  ERROR: '异常',
  PREPARED: '已准备',
  COMMITTED: '已提交',
  SUPERSEDED: '已替代',
  EXPIRED: '已过期',
  REJECTED: '已拒绝',
  CANCELLED: '已撤销',
  SUBMITTED: '已报单',
  PENDING: '待处理',
  PARTIAL_FILLED: '部分成交',
  FILLED: '全部成交',
  ACTIVE: '保护中',
  EXIT_PENDING: '退出处理中',
  PARTIALLY_EXITED: '部分退出',
  COMPLETED: '已完成',
  PAUSED: '已暂停',
  ALLOW: '允许',
  CAP: '缩减额度',
  DELAY: '延后',
  REJECT: '拒绝',
  RULE_ONLY: '固定规则',
};
const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});
const moneyFormatter = new Intl.NumberFormat('zh-CN', {
  style: 'currency',
  currency: 'CNY',
});
function time(value?: string | null) {
  if (!value) return '未记录';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? '时间不可用'
    : `${dateFormatter.format(date)}.${String(date.getUTCMilliseconds()).padStart(3, '0')}`;
}
function money(value?: number | null) {
  return value == null ? '—' : moneyFormatter.format(value);
}
function Status({ value }: { value?: string | null }) {
  return (
    <span
      className={cn(
        'text-ui-label',
        value && ['REJECT', 'REJECTED', 'ERROR', 'RECONCILE'].includes(value)
          ? 'text-amber-300'
          : 'text-slate-200'
      )}
      title={value ?? undefined}
    >
      {value ? labels[value] || value : '—'}
    </span>
  );
}
function Identity({ value }: { value?: string | null }) {
  return (
    <span className="block max-w-64 break-all font-mono text-ui-caption text-slate-400">
      {value || '未记录'}
    </span>
  );
}
function Reasons({ values }: { values?: readonly string[] | null }) {
  return values?.length ? (
    <ul className="space-y-1 text-ui-label text-amber-200">
      {[...new Set(values)].map(value => (
        <li className="break-words" key={value}>
          {labels[value] || value}
        </li>
      ))}
    </ul>
  ) : (
    <span className="text-slate-500">无已记录原因</span>
  );
}
type Row = { key: string; cells: ReactNode[] };
function FactTable({
  headers,
  rows,
  loading,
}: {
  headers: readonly string[];
  rows: Row[];
  loading: boolean;
}) {
  return (
    <Table wrapperClassName="min-h-0 flex-1" aria-label="PAPER 执行事实">
      <TableHeader className="sticky top-0 z-10 bg-card">
        <TableRow>
          {headers.map(header => (
            <TableHead className="whitespace-nowrap" key={header}>
              {header}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map(row => (
          <TableRow key={row.key}>
            {row.cells.map((cell, index) => (
              <TableCell key={headers[index]}>{cell}</TableCell>
            ))}
          </TableRow>
        ))}
        {!rows.length && (
          <TableRow>
            <TableCell colSpan={headers.length}>
              <div
                className="p-ui-section text-center text-ui-body text-slate-400"
                role="status"
              >
                {loading ? '正在加载执行事实…' : '当前执行在此分类下暂无记录'}
              </div>
            </TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  );
}
function factsTable(
  section: PaperSection,
  facts?: PaperFacts
): { headers: string[]; rows: Row[] } {
  switch (section) {
    case 'opportunities':
      return {
        headers: [
          '标的 / 候选',
          '规则评分',
          '证据',
          '原始行情时间',
          '受理时间',
          '原因',
        ],
        rows: (facts?.tAssistantPaperOpportunities?.nodes ?? []).map(item => ({
          key: item.evidenceId,
          cells: [
            <>
              <span className="font-mono">{item.instrumentCode}</span>
              <Identity value={item.candidateId} />
            </>,
            item.score == null ? '—' : item.score.toFixed(2),
            <>
              <span>
                {item.frozenEvidencePresent
                  ? '已冻结候选证据'
                  : '原冻结证据不可用'}
              </span>
              <Identity value={item.eventType} />
              <span className="text-ui-caption text-slate-500">
                评估 {time(item.evaluatedAt)}
              </span>
              <details className="text-ui-caption text-slate-400">
                <summary className="cursor-pointer text-blue-300">
                  证据标识
                </summary>
                <Identity value={item.evidenceId} />
                <Identity value={item.candidateFingerprint} />
              </details>
            </>,
            <span key="source-time" className="whitespace-nowrap font-mono">
              {time(item.sourceAt)}
            </span>,
            <span key="acceptance-time" className="whitespace-nowrap font-mono">
              {time(item.acceptedAt)}
            </span>,
            <Reasons key="reasons" values={item.reasonCodes} />,
          ],
        })),
      };
    case 'allocations':
      return {
        headers: [
          '标的 / 分配批次',
          '排名 / 结果',
          '申请上限 / 分配上限',
          '批次状态',
          '有效期 / 再评估时间',
          '原因',
        ],
        rows: (facts?.tAssistantPaperAllocations?.nodes ?? []).map(item => ({
          key: item.decisionId || item.allocationBatchId,
          cells: [
            <>
              <span className="font-mono">
                {item.instrumentCode || '整批准备中'}
              </span>
              <Identity value={item.allocationBatchId} />
              <span className="text-ui-caption text-slate-500">
                第 {item.allocationAttempt} 次分配
              </span>
            </>,
            <>
              <span className="font-mono">
                {item.rank == null ? '—' : `#${item.rank}`}{' '}
              </span>
              {item.action ? <Status value={item.action} /> : '尚未决策'}
            </>,
            <div key="amount-limits" className="whitespace-nowrap font-mono">
              {money(item.requestedAmountCeiling)}
              <br />
              {money(item.allocatedAmountCap)}
            </div>,
            <>
              <Status value={item.status} />
              <div className="text-ui-caption text-slate-400">
                提交 {time(item.committedAt)}
              </div>
            </>,
            <>
              <div className="font-mono">{time(item.expiresAt)}</div>
              {item.nextEligibleAt && (
                <div className="text-ui-caption">
                  再评估 {time(item.nextEligibleAt)}
                </div>
              )}
            </>,
            <Reasons
              key="reasons"
              values={[
                ...(item.terminalReason ? [item.terminalReason] : []),
                ...(item.reasonCodes ?? []),
              ]}
            />,
          ],
        })),
      };
    case 'reasons':
      return {
        headers: ['事件时间', '事件', '原因', '关联来源'],
        rows: (facts?.tAssistantPaperReasons?.nodes ?? []).map(item => ({
          key: item.eventId,
          cells: [
            <span key="event-time" className="whitespace-nowrap font-mono">
              {time(item.occurredAt)}
            </span>,
            <>
              {item.eventType}
              <Identity value={item.eventId} />
            </>,
            <Reasons
              key="reasons"
              values={[
                ...(item.reasonCode ? [item.reasonCode] : []),
                ...(item.reasonCodes ?? []),
              ]}
            />,
            <>
              {item.sourceType || '未记录来源'}
              <Identity value={item.sourceId} />
            </>,
          ],
        })),
      };
    case 'orders':
      return {
        headers: [
          '标的 / 方向',
          '委托 / 意图',
          '状态 / 成交量',
          '委托限价',
          '提交 / 到期',
          '最近回报行情源时间',
          '最近回报受理时间',
        ],
        rows: (facts?.tAssistantPaperOrders?.nodes ?? []).map(item => ({
          key: item.orderId,
          cells: [
            <>
              <span className="font-mono">{item.instrumentCode}</span>
              <div
                className={
                  item.side === 'BUY' ? 'text-market-up' : 'text-market-down'
                }
              >
                {item.side === 'BUY'
                  ? '买入 BUY'
                  : item.side === 'SELL'
                    ? '卖出 SELL'
                    : item.side}
              </div>
            </>,
            <>
              <Identity value={item.orderId} />
              <Identity value={item.intentId} />
              <span className="text-ui-caption text-slate-500">
                {item.ownerType === 'EXIT_PLAN' ? '退出计划' : '做 T 执行'}
              </span>
            </>,
            <>
              <Status value={item.status} />
              <div className="font-mono">
                {item.filledVolume} / {item.volume} 股
              </div>
            </>,
            <span key="limit-price" className="font-mono">
              {money(item.limitPrice)}
            </span>,
            <div key="order-times" className="whitespace-nowrap font-mono">
              {time(item.submittedAt)}
              <br />
              <span className="text-slate-500">{time(item.expiresAt)}</span>
            </div>,
            <span key="source-time" className="whitespace-nowrap font-mono">
              {time(item.sourceAt)}
            </span>,
            <span key="acceptance-time" className="whitespace-nowrap font-mono">
              {time(item.acceptedAt)}
            </span>,
          ],
        })),
      };
    case 'exitPlans':
      return {
        headers: [
          '标的 / 退出计划',
          '状态',
          '保护 / 已退 / 剩余',
          '容量检查',
          '最近评估',
          '异常原因',
        ],
        rows: (facts?.tAssistantPaperExitPlans?.nodes ?? []).map(item => ({
          key: item.planId,
          cells: [
            <>
              <span className="font-mono">{item.instrumentCode}</span>
              <Identity value={item.planId} />
            </>,
            <Status key="plan-status" value={item.status} />,
            <span
              key="protected-volumes"
              className="whitespace-nowrap font-mono"
            >
              {item.protectedVolume} / {item.exitedVolume} /{' '}
              {item.remainingVolume} 股
            </span>,
            <Status key="capacity-status" value={item.capacityStatus} />,
            <span key="evaluation-time" className="whitespace-nowrap font-mono">
              {time(item.lastEvaluatedAt)}
            </span>,
            <Reasons
              key="reasons"
              values={[
                ...(item.capacityError ? [item.capacityError] : []),
                ...(item.lastError ? [item.lastError] : []),
              ]}
            />,
          ],
        })),
      };
  }
}

export function TAssistantPaperPanel({ accountId }: { accountId: string }) {
  const state = useTAssistantPaper(accountId);
  const { execution } = state;
  const table = factsTable(state.section, state.facts);
  if (!accountId)
    return (
      <div className="p-ui-section text-ui-body text-slate-400" role="status">
        尚未配置账户，无法读取 PAPER 执行。
      </div>
    );
  return (
    <section
      className="studio-workspace-surface flex h-full min-h-0 flex-col"
      aria-label="PAPER 执行"
    >
      <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border p-ui-section">
        <div>
          <h2 className="text-ui-title font-semibold">PAPER 执行</h2>
          <p className="text-ui-caption text-slate-400">
            隔离账户事实 · 只读 · 时间为北京时间
          </p>
        </div>
        <NativeSelect
          aria-label="选择 PAPER 执行"
          className="h-control-compact min-w-48 flex-1"
          value={state.executionId}
          disabled={state.listLoading || !state.executions.length}
          onChange={event => state.selectExecution(event.target.value)}
        >
          {!state.executions.length && (
            <option value="">
              {state.listLoading ? '正在加载执行…' : '暂无 PAPER 执行'}
            </option>
          )}
          {state.executions.map(item => (
            <option key={item.executionId} value={item.executionId}>
              {time(item.createdAt)} · 配置 v{item.frozenConfigVersion} ·{' '}
              {labels[item.status] || item.status} · {item.executionId}
            </option>
          ))}
        </NativeSelect>
        <Button
          size="sm"
          variant="outline"
          disabled={state.listLoading || state.executionPage <= 1}
          onClick={state.previousExecutions}
        >
          上一页执行
        </Button>
        <span className="text-ui-caption text-slate-400">
          执行列表第 {state.executionPage} 页
        </span>
        <Button
          size="sm"
          variant="outline"
          disabled={state.listLoading || !state.hasNextExecutionPage}
          onClick={state.nextExecutions}
        >
          下一页执行
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={state.loading || state.listLoading}
          onClick={state.refresh}
        >
          {state.loading || state.listLoading ? (
            <Loader2 className="animate-spin motion-reduce:animate-none" />
          ) : (
            <RefreshCw />
          )}
          刷新
        </Button>
      </header>
      {state.error && (
        <div
          className="flex items-center gap-3 border-b border-amber-500/25 bg-amber-500/5 p-ui-section text-ui-body"
          role="alert"
        >
          <span className="flex-1 text-amber-200">
            PAPER 执行读取失败：{state.error}
          </span>
          <Button size="sm" variant="outline" onClick={state.refresh}>
            重试
          </Button>
        </div>
      )}
      {!state.executionId ? (
        <div className="p-ui-section text-ui-body text-slate-400" role="status">
          {state.listLoading
            ? '正在加载 PAPER 执行…'
            : '当前账户暂无 PAPER 执行记录。'}
        </div>
      ) : !execution ? (
        <div className="p-ui-section text-ui-body text-slate-400" role="status">
          {state.loading
            ? '正在加载执行详情…'
            : '此执行详情暂不可用，请刷新重试。'}
        </div>
      ) : (
        <>
          <div className="grid shrink-0 gap-3 border-b border-border p-ui-section text-ui-label sm:grid-cols-3">
            <div>
              执行状态：
              <Status value={execution.status} />
              <div className="mt-1">
                新买入就绪度：
                <Status value={execution.entryReadiness} />
              </div>
              <Reasons values={execution.entryReadinessReasons} />
              <div className="text-ui-caption text-slate-500">
                就绪度时点 {time(execution.entryReadinessAsOf)}
              </div>
            </div>
            <div>
              种子账户：
              {execution.seedPresent ? (
                '已初始化'
              ) : (
                <span className="text-amber-200">
                  未初始化，需要 PAPER 账户种子
                </span>
              )}
              <div className="text-ui-caption text-slate-400">
                种子时点 {time(execution.seedAsOf)}
              </div>
              <div className="text-ui-caption text-slate-400">
                账户事实时点 {time(execution.snapshotAsOf)}
              </div>
            </div>
            <div>
              <Status value={execution.scorerMode} /> · 配置 v
              {execution.frozenConfigVersion}
              <div className="text-ui-caption text-slate-400">
                规则 {execution.policyVersion} · 特征 v
                {execution.featureSchemaVersion}
              </div>
              <details className="text-ui-caption text-slate-400">
                <summary className="cursor-pointer text-blue-300">
                  冻结版本与种子标识
                </summary>
                <Identity value={execution.configVersionId} />
                <Identity value={execution.configSnapshotHash} />
                <Identity value={execution.seedSnapshotId} />
              </details>
            </div>
          </div>
          <nav
            className="flex h-studio-tab shrink-0 gap-1 border-b border-border px-ui-section"
            aria-label="PAPER 事实分类"
          >
            {sections.map(([section, label]) => (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                aria-pressed={state.section === section}
                className={cn(
                  'h-full rounded-none border-b-2',
                  state.section === section
                    ? 'border-blue-400 text-blue-200'
                    : 'border-transparent text-slate-400'
                )}
                key={section}
                onClick={() => state.selectSection(section)}
              >
                {label}
              </Button>
            ))}
          </nav>
          <p className="shrink-0 px-ui-section py-2 text-ui-caption text-slate-500">
            原始行情时间用于判断行情新鲜度；受理时间记录本地接收或账本事件时间。缺失字段显示“未记录”。
          </p>
          {!state.error && <FactTable {...table} loading={state.loading} />}
          <footer className="flex shrink-0 items-center justify-end gap-3 border-t border-border p-ui-section">
            <span className="text-ui-caption text-slate-400">
              第 {state.factPage} 页 · 本页 {table.rows.length} 条
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={state.loading || state.factPage <= 1}
              onClick={state.previousFacts}
            >
              上一页记录
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={state.loading || !state.hasNextFactPage}
              onClick={state.nextFacts}
            >
              下一页记录
            </Button>
          </footer>
        </>
      )}
    </section>
  );
}
