import {
  Ban,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
  Wallet,
} from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

import { EntryPlanCard } from '../components/EntryPlanCards';
import { EntryPlanEditor } from '../components/EntryPlanEditor';
import { EntryPlanEventTimeline } from '../components/EntryPlanEventTimeline';
import { EntryPlanModeTabs } from '../components/EntryPlanModeTabs';
import { PendingEntryIntentCard } from '../components/PendingEntryIntentCard';
import { formatEntryCurrency } from '../model/draft';
import type {
  EntryPlanController,
  EntryPlanTab,
  EntryPlanWorkspaceView,
  EntrySecurityOption,
} from '../model/types';

export interface EntryPlansPageProps {
  controller: EntryPlanController;
  view: EntryPlanWorkspaceView;
}

export function EntryPlansPage({ controller, view }: EntryPlansPageProps) {
  const [activeTab, setActiveTab] = React.useState<EntryPlanTab>('PLANS');
  const [selectedPlanId, setSelectedPlanId] = React.useState<string | null>(
    view.plans[0]?.id ?? null
  );
  const [creatingNew, setCreatingNew] = React.useState(false);
  const [selectedSecurity, setSelectedSecurity] =
    React.useState<EntrySecurityOption | null>(null);
  const [searchQuery, setSearchQuery] = React.useState('');
  const [searchResults, setSearchResults] = React.useState<
    EntrySecurityOption[]
  >([]);
  const [searching, setSearching] = React.useState(false);
  const [actionError, setActionError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (creatingNew) return;
    if (selectedPlanId && view.plans.some(plan => plan.id === selectedPlanId)) {
      return;
    }
    setSelectedPlanId(view.plans[0]?.id ?? null);
  }, [creatingNew, selectedPlanId, view.plans]);

  React.useEffect(() => {
    const normalized = searchQuery.trim();
    if (normalized.length < 2) {
      setSearchResults([]);
      return;
    }
    let active = true;
    const timeout = window.setTimeout(async () => {
      setSearching(true);
      try {
        const results = await controller.searchSecurities(normalized);
        if (active) setSearchResults(results);
      } catch (error) {
        if (active) {
          setActionError(
            error instanceof Error ? error.message : '证券搜索失败'
          );
        }
      } finally {
        if (active) setSearching(false);
      }
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(timeout);
    };
  }, [controller, searchQuery]);

  const selectedPlan =
    view.plans.find(plan => plan.id === selectedPlanId) ?? null;
  const initialDraft = React.useMemo(
    () =>
      selectedPlan
        ? {
            ...selectedPlan.editableDraft,
            planId: selectedPlan.id,
            configVersion: selectedPlan.configVersion,
            bucket: selectedPlan.bucket,
            instrumentCode: selectedPlan.instrumentCode,
            instrumentName: selectedPlan.instrumentName,
            targetMode: selectedPlan.targetMode ?? 'TARGET_POSITION_PCT',
            maxBuyPrice: selectedPlan.maxBuyPrice,
            maxTotalAmountCny: selectedPlan.maxTotalAmountCny,
            maxPositionPct: selectedPlan.maxPositionPct ?? 25,
            maxSingleIntentAmountCny:
              selectedPlan.maxSingleIntentAmountCny ?? 5000,
            maxDailyFilledAmountCny:
              selectedPlan.maxDailyFilledAmountCny ?? 10000,
            incrementalAmountCny: selectedPlan.incrementalAmountCny ?? 20000,
            additionalVolume: selectedPlan.additionalVolume ?? 100,
            strategy: selectedPlan.strategy,
            targetPositionPct: selectedPlan.targetPositionPct ?? 20,
            executionScenario: selectedPlan.executionScenario ?? 'PAPER_AUTO',
          }
        : undefined,
    [selectedPlan]
  );

  async function runAction(action: () => Promise<void>) {
    setActionError(null);
    try {
      await action();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '操作失败');
    }
  }

  return (
    <main
      className="min-h-full min-w-0 overflow-x-hidden bg-[#080d18] text-slate-100"
      data-testid="entry-plans-page"
    >
      <header className="border-b border-white/10 bg-[#07111f]/95 px-3 py-3 sm:px-4 lg:px-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-base font-black tracking-tight sm:text-lg">
              买入管理
            </h1>
            <p className="mt-1 text-xs leading-5 text-slate-400">
              托管固定标的的分批建仓与加仓。程序识别趋势，硬预算与最高买价始终由你决定。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              className="min-h-11"
              type="button"
              variant={view.globalAutoEntryPaused ? 'success' : 'destructive'}
              onClick={() =>
                runAction(() =>
                  controller.setGlobalAutoEntryPaused(
                    !view.globalAutoEntryPaused
                  )
                )
              }
            >
              <Ban />
              {view.globalAutoEntryPaused
                ? '恢复全部自动买入'
                : '暂停全部自动买入'}
            </Button>
            <Button
              aria-label="刷新买入管理数据"
              className="min-h-11"
              type="button"
              variant="outline"
              onClick={() => runAction(controller.refresh)}
            >
              <RefreshCw />
              刷新
            </Button>
          </div>
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
          {[
            ['可用资金', formatEntryCurrency(view.availableCashCny)],
            ['今日真实买入', formatEntryCurrency(view.todayFilledAmountCny)],
            ['待确认', `${view.pendingIntents.length} 笔`],
            ['运行状态', view.runtimeMessage],
          ].map(([label, value]) => (
            <div
              className="rounded-md border border-white/5 bg-white/[0.03] px-3 py-2"
              key={label}
            >
              <dt className="text-[10px] font-bold text-slate-500">{label}</dt>
              <dd className="mt-1 truncate font-mono text-xs font-black text-slate-100">
                {value}
              </dd>
            </div>
          ))}
        </dl>
        {view.globalAutoEntryPaused ? (
          <p
            className="mt-3 flex items-start gap-2 rounded-md border border-amber-400/20 bg-amber-400/10 p-2.5 text-xs text-amber-100"
            role="status"
          >
            <ShieldAlert
              aria-hidden="true"
              className="mt-0.5 h-4 w-4 shrink-0"
            />
            自动买入安全门已暂停；不会产生新的自动意图，已有委托仍等待真实回报。
          </p>
        ) : null}
        {actionError ? (
          <p className="mt-3 text-xs text-rose-300" role="alert">
            {actionError}
          </p>
        ) : null}
      </header>

      <div className="grid min-w-0 xl:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="min-w-0 border-b border-white/10 bg-[#07111f]/70 p-3 xl:min-h-[calc(100vh-184px)] xl:border-b-0 xl:border-r">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-xs font-black uppercase tracking-wider text-slate-400">
              标的与计划
            </h2>
            <span className="font-mono text-[10px] text-slate-600">
              {view.plans.length} 个
            </span>
          </div>
          <label className="sr-only" htmlFor="entry-security-search">
            搜索股票
          </label>
          <div className="relative mt-3">
            <Search
              aria-hidden="true"
              className="absolute left-3 top-3 h-4 w-4 text-slate-500"
            />
            <Input
              className="min-h-11 pl-9"
              id="entry-security-search"
              onChange={event => setSearchQuery(event.target.value)}
              placeholder="代码或名称，例如 605499"
              value={searchQuery}
            />
          </div>
          <p className="mt-1.5 text-[10px] leading-4 text-slate-600">
            可搜索未持有股票；结果来自证券主数据，不能直接输入任意代码授权实盘。
          </p>

          {searching ? (
            <p className="mt-3 text-xs text-slate-500" aria-live="polite">
              正在搜索证券主数据…
            </p>
          ) : searchResults.length > 0 ? (
            <ul
              aria-label="证券搜索结果"
              className="mt-3 space-y-1 rounded-lg border border-white/10 bg-[#0b1120] p-1"
            >
              {searchResults.map(security => (
                <li key={security.instrumentCode}>
                  <button
                    className="flex min-h-11 w-full cursor-pointer items-center justify-between gap-2 rounded-md px-2 text-left hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                    onClick={() => {
                      setSelectedSecurity(security);
                      setCreatingNew(true);
                      setSelectedPlanId(null);
                      setSearchResults([]);
                      setSearchQuery('');
                      setActiveTab('PLANS');
                    }}
                    type="button"
                  >
                    <span>
                      <span className="block text-xs font-black text-slate-100">
                        {security.instrumentName}
                      </span>
                      <span className="font-mono text-[10px] text-slate-500">
                        {security.instrumentCode}
                      </span>
                    </span>
                    <span className="text-right text-[10px] text-slate-500">
                      {security.heldVolume > 0
                        ? `持有 ${security.heldVolume} 股`
                        : '未持有'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}

          <div className="mt-4 space-y-2 xl:max-h-[calc(100vh-390px)] xl:overflow-y-auto xl:pr-1">
            {view.plans.map(plan => (
              <EntryPlanCard
                controller={controller}
                key={plan.id}
                onSelect={planId => {
                  setSelectedPlanId(planId);
                  setCreatingNew(false);
                  setSelectedSecurity(null);
                  setActiveTab('PLANS');
                }}
                plan={plan}
                selected={selectedPlanId === plan.id}
              />
            ))}
            {view.plans.length === 0 ? (
              <div className="rounded-lg border border-dashed border-white/10 p-4 text-center">
                <Plus
                  aria-hidden="true"
                  className="mx-auto h-5 w-5 text-emerald-300"
                />
                <p className="mt-2 text-xs text-slate-400">暂无买入计划</p>
                <p className="mt-1 text-[10px] leading-4 text-slate-600">
                  从上方搜索股票创建第一个暂停计划。
                </p>
              </div>
            ) : null}
          </div>
        </aside>

        <section className="min-w-0 p-3 sm:p-4 lg:p-5">
          <div className="overflow-x-auto border-b border-white/10 pb-3">
            <EntryPlanModeTabs
              activeTab={activeTab}
              onChange={setActiveTab}
              pendingCount={view.pendingIntents.length}
            />
          </div>

          <div
            aria-labelledby={`entry-plan-tab-${activeTab}`}
            className="mt-4"
            id={`entry-plan-panel-${activeTab}`}
            role="tabpanel"
          >
            {activeTab === 'PLANS' ? (
              <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_280px] 2xl:grid-cols-[minmax(0,1fr)_320px]">
                <EntryPlanEditor
                  capabilities={view.capabilities}
                  controller={controller}
                  initialDraft={initialDraft}
                  key={
                    selectedPlan
                      ? `${selectedPlan.id}:${selectedPlan.configVersion ?? 1}`
                      : `new:${selectedSecurity?.instrumentCode ?? 'empty'}`
                  }
                  onSecuritySelected={setSelectedSecurity}
                  selectedSecurity={
                    selectedSecurity ??
                    (selectedPlan
                      ? {
                          heldVolume:
                            selectedPlan.currentPositionVolume ??
                            (selectedPlan.currentPositionPct > 0 ? 1 : 0),
                          instrumentCode: selectedPlan.instrumentCode,
                          instrumentName: selectedPlan.instrumentName,
                          latestPrice: selectedPlan.latestPrice ?? null,
                        }
                      : null)
                  }
                />
                <aside
                  className="hidden self-start rounded-lg border border-emerald-400/15 bg-[#0b1120]/90 p-4 lg:sticky lg:top-4 lg:block"
                  aria-live="polite"
                >
                  <h2 className="flex items-center gap-2 text-sm font-black text-slate-100">
                    <Wallet
                      aria-hidden="true"
                      className="h-4 w-4 text-emerald-300"
                    />
                    计划摘要
                  </h2>
                  {selectedPlan ? (
                    <dl className="mt-4 space-y-3 text-xs">
                      {[
                        [
                          '计划语义',
                          selectedPlan.currentPositionPct > 0
                            ? '加仓计划'
                            : '建仓计划',
                        ],
                        [
                          '当前 / 目标',
                          `${selectedPlan.currentPositionPct.toFixed(1)}% / ${selectedPlan.targetPositionPct?.toFixed(1) ?? '--'}%`,
                        ],
                        [
                          '真实已买',
                          formatEntryCurrency(selectedPlan.filledAmountCny),
                        ],
                        [
                          '剩余预算',
                          formatEntryCurrency(
                            Math.max(
                              0,
                              selectedPlan.maxTotalAmountCny -
                                selectedPlan.filledAmountCny
                            )
                          ),
                        ],
                        [
                          '单日剩余额度',
                          formatEntryCurrency(
                            selectedPlan.dailyRemainingAmountCny
                          ),
                        ],
                        [
                          '最高可买价',
                          `¥${selectedPlan.maxBuyPrice.toFixed(2)}`,
                        ],
                        ['自动执行', selectedPlan.authorizationLabel],
                        [
                          '成交后保护',
                          selectedPlan.exitProtectionEnabled
                            ? '已配置'
                            : '未配置',
                        ],
                      ].map(([label, value]) => (
                        <div
                          className="flex items-start justify-between gap-3 border-b border-white/5 pb-2"
                          key={label}
                        >
                          <dt className="text-slate-500">{label}</dt>
                          <dd className="text-right font-mono font-bold text-slate-200">
                            {value}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  ) : (
                    <p className="mt-3 text-xs leading-5 text-slate-500">
                      选择已有计划查看权威摘要，或从左侧搜索未持有标的创建新计划。
                    </p>
                  )}
                  <p className="mt-4 rounded-md border border-cyan-400/15 bg-cyan-400/[0.06] p-2.5 text-[11px] leading-4 text-cyan-100">
                    目标仓位只补真实持仓与在途买单后的剩余缺口；ACK
                    和已报委托不会计为真实已买。
                  </p>
                </aside>
              </div>
            ) : activeTab === 'PENDING' ? (
              <div className="space-y-3">
                {view.pendingIntents.map(intent => (
                  <PendingEntryIntentCard
                    controller={controller}
                    intent={intent}
                    key={intent.id}
                  />
                ))}
                {view.pendingIntents.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-white/10 px-4 py-12 text-center text-xs text-slate-500">
                    暂无待确认买入。实盘逐笔确认计划命中规则后会显示在这里。
                  </div>
                ) : null}
              </div>
            ) : (
              <EntryPlanEventTimeline events={view.events} />
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
