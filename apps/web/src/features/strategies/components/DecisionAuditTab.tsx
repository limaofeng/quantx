import {
  AlertCircle,
  Bot,
  CandlestickChart,
  ClipboardList,
  Copy,
  GitBranch,
  ShieldAlert,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { useLocation } from 'wouter';

import { StudioMenu, useStudioMenu } from '@/components/studio-workbench';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';

import {
  getBucketLabel,
  type StrategyDecision,
  type StrategyInstance,
  type TradeIntentView,
} from '../domain';

interface DecisionAuditTabProps {
  instance?: StrategyInstance | null;
  decisions: StrategyDecision[];
}

function formatValue(value: unknown) {
  if (value === null || value === undefined || value === '') return '--';
  if (typeof value === 'number')
    return Number.isInteger(value) ? String(value) : value.toFixed(4);
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

function copyText(value: string | number | undefined | null) {
  if (value === undefined || value === null || value === '') return;
  void navigator.clipboard?.writeText(String(value));
}

function SummaryGrid({
  title,
  values,
}: {
  title: string;
  values: Record<string, unknown>;
}) {
  const entries = Object.entries(values).slice(0, 8);

  return (
    <div className="rounded-panel border border-slate-200 bg-slate-50 p-ui-section dark:border-white/10 dark:bg-white/[0.03]">
      <div className="mb-3 text-ui-micro font-black uppercase tracking-[0.24em] text-slate-400">
        {title}
      </div>
      {entries.length === 0 ? (
        <p className="text-ui-caption font-medium text-slate-500">
          后端暂未返回摘要字段。
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {entries.map(([key, value]) => (
            <div key={key} className="min-w-0">
              <div className="truncate text-ui-micro font-black uppercase tracking-widest text-slate-400">
                {key}
              </div>
              <div className="truncate text-ui-caption font-bold text-slate-700 dark:text-slate-200">
                {formatValue(value)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function DecisionAuditTab({
  instance,
  decisions,
}: DecisionAuditTabProps) {
  const [, setLocation] = useLocation();
  const { closeMenu, menu, openAtPointer } = useStudioMenu<TradeIntentView>();
  const [selectedId, setSelectedId] = useState(decisions[0]?.id);
  const selectedDecision = useMemo(
    () =>
      decisions.find(decision => decision.id === selectedId) || decisions[0],
    [decisions, selectedId]
  );

  if (!instance) {
    return (
      <Card className="p-ui-empty text-center">
        <AlertCircle className="mx-auto mb-4 h-8 w-8 text-slate-300" />
        <p className="text-ui-body font-bold text-slate-500">
          请先选择策略实例。
        </p>
      </Card>
    );
  }

  if (decisions.length === 0) {
    return (
      <Card className="rounded-panel border border-dashed border-slate-200 bg-white p-ui-empty text-center shadow-none dark:border-white/10 dark:bg-slate-900/60">
        <ClipboardList className="mx-auto mb-5 h-10 w-10 text-slate-300" />
        <h3 className="mb-2 text-ui-body font-black uppercase tracking-[0.2em] text-slate-700 dark:text-slate-200">
          暂无决策审计
        </h3>
        <p className="mx-auto max-w-lg text-ui-label font-medium leading-relaxed text-slate-500">
          当前 GraphQL
          返回值中尚未包含策略输入、策略输出或决策原因链历史。页面已切换到新策略语义，
          后端补齐字段后会直接显示。
        </p>
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-ui-panel lg:grid-cols-[280px_1fr]">
      <Card className="flex max-h-[520px] min-h-0 flex-col overflow-hidden rounded-panel border border-slate-200 bg-white p-ui-section shadow-none dark:border-white/10 dark:bg-slate-900/60">
        <div className="mb-4 shrink-0 px-2">
          <div className="text-ui-micro font-black uppercase tracking-[0.28em] text-blue-500">
            决策审计
          </div>
          <p className="mt-1 text-ui-caption font-medium text-slate-500">
            最近 {decisions.length} 次策略步进输出
          </p>
        </div>
        <div className="custom-scrollbar min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
          {decisions.map(decision => (
            <Button
              key={decision.id}
              variant="ghost"
              className={`h-auto w-full justify-start rounded-panel px-3 py-3 text-left ${
                selectedDecision?.id === decision.id
                  ? 'bg-blue-600 text-white hover:bg-blue-600 hover:text-white'
                  : 'text-slate-500 hover:bg-slate-50 dark:hover:bg-white/5'
              }`}
              onClick={() => setSelectedId(decision.id)}
            >
              <div className="min-w-0">
                <div className="truncate text-ui-caption font-black">
                  {new Date(decision.decidedAt).toLocaleString('zh-CN')}
                </div>
                <div className="mt-1 text-ui-micro font-bold uppercase tracking-widest opacity-70">
                  策略意图 {decision.tradeIntents.length}
                </div>
              </div>
            </Button>
          ))}
        </div>
      </Card>

      {selectedDecision && (
        <div className="space-y-ui-section">
          <Card className="rounded-panel border border-slate-200 bg-white p-ui-panel shadow-none dark:border-white/10 dark:bg-slate-900/60">
            <div className="mb-5 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <div className="text-ui-micro font-black uppercase tracking-[0.3em] text-blue-500">
                  策略基类执行
                </div>
                <h3 className="mt-1 text-ui-heading font-black text-slate-900 dark:text-white">
                  {instance.displayName}
                </h3>
              </div>
              <Badge
                variant="outline"
                className="w-fit rounded-lg border-blue-500/30 bg-blue-500/5 px-3 py-1 text-ui-micro font-black uppercase tracking-widest text-blue-500"
              >
                策略意图，不是委托或成交
              </Badge>
            </div>

            <div className="grid grid-cols-1 gap-ui-section lg:grid-cols-2">
              <SummaryGrid
                title="输入摘要"
                values={selectedDecision.inputSummary}
              />
              <SummaryGrid
                title="输出摘要"
                values={selectedDecision.outputSummary}
              />
            </div>
          </Card>

          <Card className="overflow-hidden rounded-panel border border-slate-200 bg-white shadow-none dark:border-white/10 dark:bg-slate-900/60">
            <div className="border-b border-slate-100 px-ui-panel py-ui-section dark:border-white/5">
              <div className="flex items-center gap-3">
                <GitBranch className="h-4 w-4 text-blue-500" />
                <h3 className="text-ui-caption font-black uppercase tracking-[0.24em] text-slate-700 dark:text-slate-200">
                  策略意图明细
                </h3>
              </div>
            </div>
            <div className="overflow-x-auto">
              <div className="min-w-[760px]">
                <div className="grid grid-cols-12 gap-3 border-b border-slate-100 bg-slate-50 px-ui-panel py-3 text-ui-micro font-black uppercase tracking-[0.2em] text-slate-400 dark:border-white/5 dark:bg-white/[0.02]">
                  <div className="col-span-2">标的</div>
                  <div className="col-span-1">方向</div>
                  <div className="col-span-2">目标仓</div>
                  <div className="col-span-2 text-right">意图价格</div>
                  <div className="col-span-2 text-right">意图数量</div>
                  <div className="col-span-3">原因</div>
                </div>
                {selectedDecision.tradeIntents.length === 0 ? (
                  <div className="px-ui-panel py-ui-empty text-center text-ui-label font-bold text-slate-400">
                    本次决策未输出策略意图。
                  </div>
                ) : (
                  selectedDecision.tradeIntents.map(intent => (
                    <div
                      key={intent.id}
                      className="grid grid-cols-12 gap-3 border-b border-slate-100 px-ui-panel py-ui-section text-ui-caption font-bold transition-colors last:border-b-0 hover:bg-slate-50 dark:border-white/5 dark:hover:bg-white/[0.04]"
                      onContextMenu={event => openAtPointer(event, intent)}
                    >
                      <div className="col-span-2 font-mono text-slate-700 dark:text-slate-200">
                        {intent.instrumentCode}
                      </div>
                      <div className="col-span-1">
                        <Badge
                          variant="outline"
                          className={`rounded-full text-ui-micro font-black ${
                            intent.side === 'BUY'
                              ? 'border-market-up/30 bg-market-up/5 text-market-up'
                              : 'border-market-down/30 bg-market-down/5 text-market-down'
                          }`}
                        >
                          {intent.side === 'BUY'
                            ? '买入'
                            : intent.side === 'SELL'
                              ? '卖出'
                              : intent.side}
                        </Badge>
                      </div>
                      <div className="col-span-2 text-slate-500">
                        {getBucketLabel(intent.targetBucket)}
                      </div>
                      <div className="col-span-2 text-right font-mono text-slate-500">
                        {formatValue(intent.priceIntent)}
                      </div>
                      <div className="col-span-2 text-right font-mono text-slate-500">
                        {formatValue(intent.quantityIntent)}
                      </div>
                      <div className="col-span-3 min-w-0 text-slate-600 dark:text-slate-300">
                        <div className="truncate">
                          {intent.reason || '未提供原因'}
                        </div>
                        {intent.traceId && (
                          <div className="mt-1 truncate text-ui-micro font-mono text-slate-400">
                            {intent.traceId}
                          </div>
                        )}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </Card>

          <Card className="rounded-panel border border-slate-200 bg-white p-ui-panel shadow-none dark:border-white/10 dark:bg-slate-900/60">
            <div className="mb-4 flex items-center gap-3">
              <ShieldAlert className="h-4 w-4 text-amber-500" />
              <h3 className="text-ui-caption font-black uppercase tracking-[0.24em] text-slate-700 dark:text-slate-200">
                决策原因链
              </h3>
            </div>
            {selectedDecision.decisionTrace.length === 0 ? (
              <p className="text-ui-label font-medium text-slate-500">
                后端暂未返回原因链；拒单、少买、不买和熔断原因应在此处审计。
              </p>
            ) : (
              <div className="space-y-3">
                {selectedDecision.decisionTrace.map((item, index) => (
                  <div key={`${item}-${index}`} className="flex gap-3">
                    <div className="mt-1 h-5 w-5 shrink-0 rounded-full bg-blue-500/10 text-center text-ui-caption font-black leading-5 text-blue-500">
                      {index + 1}
                    </div>
                    <p className="text-ui-label font-medium leading-relaxed text-slate-600 dark:text-slate-300">
                      {item}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      )}

      <StudioMenu
        ariaLabel="策略意图菜单"
        menu={menu}
        onClose={closeMenu}
        width={216}
        items={[
          {
            id: 'open-stock',
            label: '查看标的详情',
            icon: <CandlestickChart size={14} />,
            onSelect: () =>
              menu?.payload?.instrumentCode &&
              setLocation(`/stock/${menu.payload.instrumentCode}`),
          },
          {
            id: 'create-strategy',
            label: '创建同标的策略',
            icon: <Bot size={14} />,
            onSelect: () =>
              menu?.payload?.instrumentCode &&
              setLocation(
                `/strategies/run?symbol=${menu.payload.instrumentCode}`
              ),
          },
          { id: 'sep-copy', type: 'separator' },
          {
            id: 'copy-intent',
            label: '复制意图 ID',
            icon: <Copy size={14} />,
            onSelect: () => copyText(menu?.payload?.id),
          },
          {
            id: 'copy-code',
            label: '复制标的代码',
            icon: <Copy size={14} />,
            onSelect: () => copyText(menu?.payload?.instrumentCode),
          },
          {
            id: 'copy-trace',
            label: '复制 Trace ID',
            icon: <Copy size={14} />,
            disabled: !menu?.payload?.traceId,
            onSelect: () => copyText(menu?.payload?.traceId),
          },
          {
            id: 'copy-reason',
            label: '复制原因',
            icon: <Copy size={14} />,
            disabled: !menu?.payload?.reason,
            onSelect: () => copyText(menu?.payload?.reason),
          },
        ]}
      />
    </div>
  );
}
