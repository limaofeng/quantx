import { Loader2, X } from 'lucide-react';
import { useQuery } from 'urql';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { StrategyInstrumentSelector } from '@/features/strategies/components/StrategyInstrumentSelector';
import { cn } from '@/utils/cn';

import { TTradeInstrumentNameQuery } from '../../hooks/useTTradeGlobal';

import {
  formatNumber,
  hasInstrumentName,
  resolveInstrumentName,
} from './utils';

function InstrumentNameLabel({
  className,
  knownName,
  stockCode,
}: {
  className?: string;
  knownName?: string | null;
  stockCode: string;
}) {
  const needsLookup = !hasInstrumentName(stockCode, knownName);
  const [result] = useQuery({
    query: TTradeInstrumentNameQuery,
    variables: { stockCode },
    pause: !needsLookup,
    requestPolicy: 'cache-first',
  });
  const instrumentName = resolveInstrumentName(
    stockCode,
    result.data?.instrument?.name,
    knownName
  );
  return <div className={className}>{instrumentName}</div>;
}

export interface ReplayPortfolioPositionContext {
  avgPrice: number;
  availableVolume: number;
  instrumentName: string;
  marketValue: number;
  stockCode: string;
  volume: number;
}

export interface ReplayManualPositionDraft {
  avgPrice: string;
  instrumentName: string;
  stockCode: string;
  volume: string;
}

export interface ReplaySidebarEditorContext {
  manualCash: string;
  manualPositions: ReplayManualPositionDraft[];
  onAddPosition: (
    stockCode: string,
    instrumentName: string,
    avgPrice: string
  ) => void;
  onCashChange: (value: string) => void;
  onPositionChange: (
    index: number,
    field: 'avgPrice' | 'volume',
    value: string
  ) => void;
  onPositionRemove: (index: number) => void;
  onSourceChange: (source: 'MANUAL' | 'SNAPSHOT') => void;
  previousTradingDate: string;
  requiresManualPortfolio: boolean;
  snapshotAvailable: boolean;
}

export interface ReplaySidebarContext {
  accountId: string;
  asOf: string;
  cashAvailable: number;
  editor: ReplaySidebarEditorContext | null;
  frozen: boolean;
  loading: boolean;
  message: string;
  mode: 'CREATE' | 'VIEW';
  positions: ReplayPortfolioPositionContext[];
  source: 'MANUAL' | 'SNAPSHOT';
  totalAsset: number;
}

function ReplayPositionList({
  emptyLabel,
  positions,
}: {
  emptyLabel: string;
  positions: ReplayPortfolioPositionContext[];
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto custom-scrollbar">
      {positions.map(position => (
        <div
          key={position.stockCode}
          className="border-b border-white/[0.05] px-ui-section py-2.5"
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <InstrumentNameLabel
                className="truncate text-ui-caption font-bold text-slate-200"
                knownName={position.instrumentName}
                stockCode={position.stockCode}
              />
              <div className="font-mono text-ui-micro text-slate-600">
                {position.stockCode}
              </div>
            </div>
            <div className="shrink-0 text-right font-mono text-ui-caption">
              <div className="text-slate-300">{position.volume} 股</div>
              <div className="mt-0.5 text-slate-600">
                成本 {formatNumber(position.avgPrice, 3)}
              </div>
            </div>
          </div>
          <div className="mt-2 flex justify-between font-mono text-ui-micro text-slate-600">
            <span>可用 {position.availableVolume}</span>
            <span>市值 ¥{formatNumber(position.marketValue)}</span>
          </div>
        </div>
      ))}
      {positions.length === 0 && (
        <div className="px-ui-section py-ui-empty text-center text-ui-caption text-slate-600">
          {emptyLabel}
        </div>
      )}
    </div>
  );
}

function ReplayAccountEditor({
  context,
  editor,
}: {
  context: ReplaySidebarContext;
  editor: ReplaySidebarEditorContext;
}) {
  const tradeableCount = context.positions.filter(
    position => position.volume >= 100
  ).length;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto custom-scrollbar">
        <section className="border-b border-white/[0.05] p-ui-section">
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <div className="text-ui-micro font-bold uppercase tracking-[0.12em] text-slate-600">
                账户
              </div>
              <div className="mt-1 font-mono text-ui-label text-slate-300">
                {context.accountId || '--'}
              </div>
            </div>
            <div>
              <div className="text-ui-micro text-slate-600">组合时点</div>
              <div className="mt-1 font-mono text-ui-caption text-slate-300">
                {context.asOf || editor.previousTradingDate || '--'}
              </div>
            </div>
            <div>
              <div className="text-ui-micro text-slate-600">预计总资产</div>
              <div className="mt-1 font-mono text-ui-caption text-slate-300">
                ¥{formatNumber(context.totalAsset)}
              </div>
            </div>
          </div>
        </section>

        <section className="border-b border-white/[0.05] p-ui-section">
          <div className="mb-2 text-ui-caption font-bold text-slate-300">
            账户来源
          </div>
          <div
            role="group"
            aria-label="回测账户来源"
            className="grid h-control-compact grid-cols-2 overflow-hidden border border-white/10"
          >
            <button
              type="button"
              disabled={!editor.snapshotAvailable}
              onClick={() => editor.onSourceChange('SNAPSHOT')}
              className={cn(
                'cursor-pointer text-ui-caption font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70 disabled:cursor-not-allowed disabled:opacity-35',
                context.source === 'SNAPSHOT'
                  ? 'bg-blue-500/15 text-blue-200'
                  : 'text-slate-500 hover:bg-white/[0.05] hover:text-slate-200'
              )}
            >
              D-1 快照
            </button>
            <button
              type="button"
              onClick={() => editor.onSourceChange('MANUAL')}
              className={cn(
                'cursor-pointer border-l border-white/10 text-ui-caption font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70',
                context.source === 'MANUAL'
                  ? 'bg-blue-500/15 text-blue-200'
                  : 'text-slate-500 hover:bg-white/[0.05] hover:text-slate-200'
              )}
            >
              手工组合
            </button>
          </div>
        </section>

        {context.source === 'SNAPSHOT' ? (
          <>
            <section className="border-b border-white/[0.05] p-ui-section">
              {context.loading ? (
                <div
                  aria-label="正在读取初始回测账户"
                  className="flex items-center gap-2 text-ui-caption text-slate-500"
                >
                  <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
                  正在读取 D-1 账户快照…
                </div>
              ) : (
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <div className="text-ui-micro text-slate-600">
                        可用资金
                      </div>
                      <div className="mt-1 font-mono text-ui-label font-bold text-slate-200">
                        ¥{formatNumber(context.cashAvailable)}
                      </div>
                    </div>
                    <div>
                      <div className="text-ui-micro text-slate-600">可做 T</div>
                      <div className="mt-1 font-mono text-ui-label font-bold text-slate-200">
                        {tradeableCount} 只
                      </div>
                    </div>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => editor.onSourceChange('MANUAL')}
                    className="h-control-compact w-full rounded-sm border-blue-400/25 text-ui-caption text-blue-200 hover:bg-blue-500/10"
                  >
                    导入并编辑
                  </Button>
                </div>
              )}
            </section>
            <div className="flex h-10 items-center justify-between border-b border-white/[0.05] px-ui-section">
              <span className="text-ui-caption font-bold text-slate-300">
                快照持仓
              </span>
              <span className="font-mono text-ui-micro text-slate-600">
                {context.positions.length} 只
              </span>
            </div>
            <ReplayPositionList
              emptyLabel="当前快照没有持仓明细"
              positions={context.positions}
            />
          </>
        ) : (
          <>
            <section className="border-b border-white/[0.05] p-ui-section">
              <Label
                htmlFor="replay-sidebar-cash"
                className="text-ui-caption text-slate-400"
              >
                可用资金
              </Label>
              <Input
                id="replay-sidebar-cash"
                type="number"
                min="0"
                value={editor.manualCash}
                onChange={event => editor.onCashChange(event.target.value)}
                className="mt-1 h-control-compact rounded-sm border-white/10 bg-[#050b16] font-mono text-ui-label focus-visible:ring-blue-400/70"
              />
              <p className="mt-2 text-ui-caption leading-5 text-slate-600">
                {editor.previousTradingDate || '--'}{' '}
                收盘；开盘前持仓按已结算库存处理。
              </p>
            </section>

            <div className="flex h-10 items-center justify-between border-b border-white/[0.05] px-ui-section">
              <span className="text-ui-caption font-bold text-slate-300">
                持仓明细
              </span>
              <span className="font-mono text-ui-micro text-slate-600">
                {editor.manualPositions.length} 只
              </span>
            </div>
            <div>
              {editor.manualPositions.map((position, index) => (
                <article
                  key={position.stockCode}
                  className="border-b border-white/[0.05] p-ui-section"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-ui-caption font-bold text-slate-200">
                        {position.instrumentName || position.stockCode}
                      </div>
                      <div className="font-mono text-ui-micro text-slate-600">
                        {position.stockCode}
                      </div>
                    </div>
                    <button
                      type="button"
                      aria-label={`删除 ${position.stockCode}`}
                      onClick={() => editor.onPositionRemove(index)}
                      className="flex h-control-compact w-control-compact cursor-pointer items-center justify-center text-slate-600 transition-colors hover:bg-rose-500/10 hover:text-rose-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    <div>
                      <Label
                        htmlFor={`replay-volume-${index}`}
                        className="text-ui-micro text-slate-600"
                      >
                        持仓股数
                      </Label>
                      <Input
                        id={`replay-volume-${index}`}
                        type="number"
                        min="1"
                        step="1"
                        value={position.volume}
                        onChange={event =>
                          editor.onPositionChange(
                            index,
                            'volume',
                            event.target.value
                          )
                        }
                        className="mt-1 h-control-compact rounded-sm border-white/10 bg-[#050b16] font-mono text-ui-caption focus-visible:ring-blue-400/70"
                      />
                    </div>
                    <div>
                      <Label
                        htmlFor={`replay-cost-${index}`}
                        className="text-ui-micro text-slate-600"
                      >
                        平均成本
                      </Label>
                      <Input
                        id={`replay-cost-${index}`}
                        type="number"
                        min="0.001"
                        step="0.001"
                        value={position.avgPrice}
                        onChange={event =>
                          editor.onPositionChange(
                            index,
                            'avgPrice',
                            event.target.value
                          )
                        }
                        className="mt-1 h-control-compact rounded-sm border-white/10 bg-[#050b16] font-mono text-ui-caption focus-visible:ring-blue-400/70"
                      />
                    </div>
                  </div>
                </article>
              ))}
              {editor.manualPositions.length === 0 && (
                <div className="px-ui-section py-ui-empty text-center text-ui-caption text-slate-600">
                  尚未添加持仓
                </div>
              )}
              <div className="border-b border-white/[0.05] p-ui-section">
                <StrategyInstrumentSelector
                  value=""
                  onChange={(stockCode, stock) => {
                    if (!stockCode) return;
                    editor.onAddPosition(
                      stockCode,
                      stock?.name || stockCode,
                      String(stock?.quote?.lastPrice || '')
                    );
                  }}
                  inputClassName="h-control-compact rounded-sm border-white/10 bg-[#050b16] text-ui-caption"
                  placeholder="搜索股票代码或名称并加入持仓"
                />
              </div>
              <div className="px-ui-section py-2 text-ui-caption leading-5 text-slate-600">
                {editor.manualPositions.length} 只持仓 · {tradeableCount} 只可做
                T；不足 100 股仅计入账户权益。
              </div>
            </div>
          </>
        )}

        {editor.requiresManualPortfolio && (
          <div className="m-ui-section border border-amber-400/15 bg-amber-400/[0.035] px-3 py-2">
            <p className="text-ui-caption font-bold text-amber-100">
              缺少可审计的历史初始组合
            </p>
            <p className="mt-0.5 text-ui-caption leading-5 text-amber-200/55">
              当前没有可采用的 D-1 日结快照，请使用手工组合配置回测账户。
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

export function TTradeReplaySidebar({
  accountId,
  context,
}: {
  accountId: string;
  context: ReplaySidebarContext | null;
}) {
  const positions = context?.positions || [];
  const creating = context?.mode === 'CREATE';

  return (
    <aside className="studio-workspace-surface flex h-full min-h-0 flex-col">
      <div className="flex h-[68px] shrink-0 items-center justify-between border-b border-white/[0.05] px-ui-section">
        <div>
          <div className="text-ui-caption font-black uppercase tracking-[0.18em] text-cyan-300">
            Replay Lab
          </div>
          <h1 className="mt-1 text-ui-title font-black text-slate-100">
            {creating
              ? '新增回测账户'
              : context?.frozen
                ? '冻结初始账户'
                : '初始回测账户'}
          </h1>
        </div>
        {context && (
          <span className="border border-blue-400/20 bg-blue-500/[0.07] px-1.5 py-0.5 text-ui-micro font-black text-blue-200">
            {creating
              ? '新增'
              : context.source === 'SNAPSHOT'
                ? 'D-1 快照'
                : '手工组合'}
          </span>
        )}
      </div>

      {creating && context?.editor ? (
        <ReplayAccountEditor context={context} editor={context.editor} />
      ) : (
        <>
          <div className="shrink-0 border-b border-white/[0.05] p-ui-section">
            {context?.loading ? (
              <div
                aria-label="正在读取初始回测账户"
                className="flex items-center gap-2 text-ui-caption text-slate-500"
              >
                <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
                正在读取初始账户…
              </div>
            ) : (
              <div className="space-y-3">
                <div>
                  <div className="text-ui-micro font-bold uppercase tracking-[0.12em] text-slate-600">
                    账户
                  </div>
                  <div className="mt-1 font-mono text-ui-label text-slate-300">
                    {context?.accountId || accountId || '--'}
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <div className="text-ui-micro text-slate-600">组合时点</div>
                    <div className="mt-1 font-mono text-ui-caption text-slate-300">
                      {context?.asOf || '--'}
                    </div>
                  </div>
                  <div>
                    <div className="text-ui-micro text-slate-600">持仓</div>
                    <div className="mt-1 font-mono text-ui-caption text-slate-300">
                      {positions.length} 只
                    </div>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3 border-t border-white/[0.05] pt-3">
                  <div>
                    <div className="text-ui-micro text-slate-600">可用资金</div>
                    <div className="mt-1 font-mono text-ui-label font-bold text-slate-200">
                      ¥{formatNumber(context?.cashAvailable || 0)}
                    </div>
                  </div>
                  <div>
                    <div className="text-ui-micro text-slate-600">总资产</div>
                    <div className="mt-1 font-mono text-ui-label font-bold text-slate-200">
                      ¥{formatNumber(context?.totalAsset || 0)}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex h-10 shrink-0 items-center justify-between border-b border-white/[0.05] px-ui-section">
              <span className="text-ui-caption font-bold text-slate-300">
                持仓明细
              </span>
              <span className="font-mono text-ui-micro text-slate-600">
                {positions.length} 只
              </span>
            </div>
            <ReplayPositionList
              emptyLabel="当前初始账户没有持仓明细"
              positions={positions}
            />
          </div>
          <div className="shrink-0 border-t border-white/[0.06] bg-[#091322] p-3 text-ui-caption leading-5 text-slate-500">
            {context?.message || '选择日期后读取开始日前的账户日结快照。'}
          </div>
        </>
      )}

      {creating && (
        <div className="shrink-0 border-t border-white/[0.06] bg-[#091322] p-3 text-ui-caption leading-5 text-slate-500">
          {context?.message || '选择日期后读取开始日前的账户日结快照。'}
        </div>
      )}
    </aside>
  );
}
