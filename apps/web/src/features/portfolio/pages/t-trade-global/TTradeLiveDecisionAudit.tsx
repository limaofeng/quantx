import { Database, Loader2, RefreshCw, ShieldAlert } from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';
import type {
  ExecutionTraceView,
  StrategyDecision,
} from '@/features/strategies/domain';

import { replayReasonLabel } from './replayEvidencePresentation';
import {
  replayDecisionInstrumentCode,
  replayDecisionReason,
} from './replayWorkspace';
import {
  decisionExecutionStatusLabels,
  TTradeDecisionAuditTable,
  type TTradeDecisionAuditRecord,
} from './TTradeDecisionAuditTable';

type IntentFilter = 'ALL' | 'WITH_INTENT' | 'WITHOUT_INTENT';

function executionStatus(
  decision: StrategyDecision,
  executions: readonly ExecutionTraceView[]
) {
  const intent = decision.tradeIntents[0];
  if (!intent) return '';
  const execution = executions.find(item => item.intentId === intent.id);
  return (
    execution?.orderStatus ||
    execution?.fillStatus ||
    intent.status ||
    'PENDING'
  );
}

function AuditFilterSelect({
  label,
  onChange,
  options,
  value,
}: {
  label: string;
  onChange: (value: string) => void;
  options: readonly (readonly [string, string])[];
  value: string;
}) {
  return (
    <NativeSelect
      aria-label={label}
      className="h-control-compact w-auto min-w-32 rounded-sm border-white/[0.1] bg-[#0b1628] text-ui-label text-slate-200"
      value={value}
      onChange={event => onChange(event.target.value)}
    >
      <option value="">{label} · 全部</option>
      {options.map(([optionValue, optionLabel]) => (
        <option key={optionValue} value={optionValue}>
          {optionLabel}
        </option>
      ))}
    </NativeSelect>
  );
}

export function TTradeLiveDecisionAudit({
  decisions,
  error,
  executions,
  instrumentNames,
  loading,
  onRefresh,
  runId,
}: {
  decisions: readonly StrategyDecision[];
  error?: string | null;
  executions: readonly ExecutionTraceView[];
  instrumentNames: ReadonlyMap<string, string>;
  loading: boolean;
  onRefresh: () => void;
  runId?: string | null;
}) {
  const [expandedId, setExpandedId] = React.useState<string | null>(null);
  const [stockCode, setStockCode] = React.useState('');
  const [intentFilter, setIntentFilter] = React.useState<IntentFilter>('ALL');
  const [statusFilter, setStatusFilter] = React.useState('');
  const [search, setSearch] = React.useState('');

  React.useEffect(() => {
    setExpandedId(null);
    setStockCode('');
    setIntentFilter('ALL');
    setStatusFilter('');
    setSearch('');
  }, [runId]);

  React.useEffect(() => {
    if (!expandedId) return;
    const collapseOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setExpandedId(null);
    };
    window.addEventListener('keydown', collapseOnEscape);
    return () => window.removeEventListener('keydown', collapseOnEscape);
  }, [expandedId]);

  const records = React.useMemo<TTradeDecisionAuditRecord[]>(
    () =>
      decisions.map(decision => {
        const intentIds = new Set(
          decision.tradeIntents.map(intent => intent.id)
        );
        return {
          decision,
          executions: executions.filter(item => intentIds.has(item.intentId)),
        };
      }),
    [decisions, executions]
  );
  const instrumentCodes = React.useMemo(
    () =>
      Array.from(
        new Set(
          decisions
            .map(replayDecisionInstrumentCode)
            .filter((value): value is string => Boolean(value))
        )
      ).sort(),
    [decisions]
  );
  const filteredRecords = React.useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    return records.filter(record => {
      const { decision } = record;
      const instrumentCode = replayDecisionInstrumentCode(decision);
      if (stockCode && instrumentCode !== stockCode) return false;
      if (
        intentFilter === 'WITH_INTENT' &&
        decision.tradeIntents.length === 0
      ) {
        return false;
      }
      if (
        intentFilter === 'WITHOUT_INTENT' &&
        decision.tradeIntents.length > 0
      ) {
        return false;
      }
      const status = executionStatus(decision, record.executions);
      if (statusFilter && status !== statusFilter) return false;
      if (!normalizedSearch) return true;
      const searchable = [
        instrumentCode,
        instrumentNames.get(instrumentCode.toUpperCase()),
        decision.id,
        replayDecisionReason(decision),
        replayReasonLabel(replayDecisionReason(decision)),
        ...decision.decisionTrace,
        ...decision.decisionTrace.map(replayReasonLabel),
        ...decision.tradeIntents.flatMap(intent => [
          intent.id,
          intent.reason || '',
          intent.instrumentCode,
        ]),
      ]
        .join(' ')
        .toLowerCase();
      return searchable.includes(normalizedSearch);
    });
  }, [instrumentNames, intentFilter, records, search, statusFilter, stockCode]);
  const withIntentCount = decisions.filter(
    decision => decision.tradeIntents.length > 0
  ).length;
  const riskBlockedCount = records.filter(record =>
    record.executions.some(item =>
      /REJECT|BLOCK|KILL|FAIL/.test(
        `${item.riskDecision || ''} ${item.orderStatus || ''}`.toUpperCase()
      )
    )
  ).length;
  const filtersActive = Boolean(
    stockCode || intentFilter !== 'ALL' || statusFilter || search
  );

  return (
    <section
      className="studio-workspace-surface flex h-full min-h-0 flex-col"
      aria-label="实盘决策审计"
    >
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-white/[0.08] px-ui-section py-3">
        <div>
          <h2 className="text-ui-body font-bold text-slate-100">决策审计</h2>
          <p className="mt-0.5 text-ui-caption text-slate-400">
            与回测共用同一决策展示；实盘列展示
            OrderSizer、风控、真实委托与券商成交回报。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {[
            ['决策记录', decisions.length],
            ['产生意图', withIntentCount],
            ['未发意图', decisions.length - withIntentCount],
            ['风险阻断', riskBlockedCount],
          ].map(([label, value]) => (
            <span
              key={label}
              className="border border-white/[0.09] bg-white/[0.025] px-2 py-1 text-ui-caption text-slate-300"
            >
              {label} <span className="font-mono text-slate-100">{value}</span>
            </span>
          ))}
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-control-compact rounded-sm text-ui-caption"
            disabled={loading}
            onClick={onRefresh}
          >
            {loading ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
            ) : (
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            )}
            刷新
          </Button>
        </div>
      </header>

      {error && (
        <div
          role="alert"
          className="flex shrink-0 items-start gap-2 border-b border-rose-400/20 bg-rose-400/[0.06] px-ui-section py-2.5 text-ui-caption leading-5 text-rose-100"
        >
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          决策审计读取失败；
          {decisions.length > 0
            ? '当前仍显示上次成功读取的记录。'
            : '当前没有可展示的决策记录。'}
        </div>
      )}

      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-white/[0.08] p-3">
        <AuditFilterSelect
          label="标的"
          value={stockCode}
          options={instrumentCodes.map(code => [
            code,
            instrumentNames.get(code.toUpperCase())
              ? `${instrumentNames.get(code.toUpperCase())} ${code}`
              : code,
          ])}
          onChange={setStockCode}
        />
        <AuditFilterSelect
          label="决策"
          value={intentFilter === 'ALL' ? '' : intentFilter}
          options={[
            ['WITH_INTENT', '产生意图'],
            ['WITHOUT_INTENT', '未发意图'],
          ]}
          onChange={value => setIntentFilter((value || 'ALL') as IntentFilter)}
        />
        <AuditFilterSelect
          label="执行状态"
          value={statusFilter}
          options={Object.entries(decisionExecutionStatusLabels)}
          onChange={setStatusFilter}
        />
        <Input
          aria-label="搜索实盘决策审计"
          value={search}
          onChange={event => setSearch(event.target.value)}
          placeholder="标的、原因或关联 ID"
          className="h-control-compact min-w-40 flex-1 text-ui-label"
        />
        {filtersActive && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setStockCode('');
              setIntentFilter('ALL');
              setStatusFilter('');
              setSearch('');
            }}
          >
            清除筛选
          </Button>
        )}
        <div className="w-full truncate font-mono text-ui-caption text-slate-400">
          当前运行 · {runId || '尚未创建实时策略运行'}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto custom-scrollbar">
        {!runId ? (
          <div className="flex min-h-64 flex-col items-center justify-center px-ui-empty text-center">
            <Database className="h-9 w-9 text-slate-700" />
            <h3 className="mt-3 text-ui-body font-bold text-slate-300">
              暂无实时策略运行
            </h3>
            <p className="mt-1 max-w-md text-ui-caption leading-5 text-slate-400">
              启动全局监控后，每次材料决策都会进入这里；未发意图也会保留。
            </p>
          </div>
        ) : loading && decisions.length === 0 ? (
          <div
            role="status"
            aria-busy="true"
            className="flex min-h-64 items-center justify-center text-ui-label text-slate-400"
          >
            <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
            读取实时决策审计…
          </div>
        ) : filteredRecords.length === 0 ? (
          <div className="flex min-h-64 flex-col items-center justify-center px-ui-empty text-center">
            <Database className="h-9 w-9 text-slate-700" />
            <h3 className="mt-3 text-ui-body font-bold text-slate-300">
              {filtersActive ? '当前筛选没有匹配记录' : '暂无材料决策'}
            </h3>
            <p className="mt-1 max-w-md text-ui-caption leading-5 text-slate-400">
              {filtersActive
                ? '清除筛选可查看当前运行的全部决策。'
                : '普通 Tick 可由权威行情与检查点确定性重放；材料决策会在这里持久化。'}
            </p>
          </div>
        ) : (
          <TTradeDecisionAuditTable
            executionMode="LIVE"
            expandedId={expandedId}
            instrumentNames={instrumentNames}
            onToggle={decisionId =>
              setExpandedId(current =>
                current === decisionId ? null : decisionId
              )
            }
            records={filteredRecords}
          />
        )}
      </div>
      <div className="sr-only" aria-live="polite">
        实盘决策审计已刷新，共 {filteredRecords.length} 条
      </div>
    </section>
  );
}
