import { Hand, Layers3, TrendingUp } from 'lucide-react';
import * as React from 'react';

import { cn } from '@/utils/cn';

import type {
  EntryPlanStrategy,
  EntryRuleCapabilityView,
} from '../model/types';

const strategies: Array<{
  code: EntryPlanStrategy;
  title: string;
  description: string;
  suitableFor: string;
  automation: string;
  userDecision: string;
  icon: React.ElementType;
  recommended?: boolean;
}> = [
  {
    code: 'TREND_PULLBACK_CONFIRMATION',
    title: '趋势回撤建仓',
    description: '上涨趋势成立时等待回撤企稳，避免追逐瞬时拉升。',
    suitableFor: '趋势股的分批核心仓建仓',
    automation: '趋势评分、回撤确认、量价强弱与节奏调整',
    userDecision: '目标仓位、预算和最高可买价',
    icon: TrendingUp,
    recommended: true,
  },
  {
    code: 'PRICE_LADDER',
    title: '价格阶梯建仓',
    description: '到达你设定的价格档位时逐档买入，每档只处理一批。',
    suitableFor: '已有明确可接受价格区间的计划',
    automation: '档位触发、防重复与批次冷却',
    userDecision: '各档价格和每档额度',
    icon: Layers3,
  },
  {
    code: 'MANUAL_TRIGGER',
    title: '人工触发',
    description: '系统保存预算和风控边界，由你决定每批启动时点。',
    suitableFor: '先验证资金与执行链路',
    automation: '下单前尺寸规范化和实时风控',
    userDecision: '每批的启动时点',
    icon: Hand,
  },
];

export function EntryStrategyPicker({
  capabilities,
  onChange,
  value,
}: {
  capabilities?: EntryRuleCapabilityView[];
  onChange: (value: EntryPlanStrategy) => void;
  value: EntryPlanStrategy;
}) {
  const refs = React.useRef<Array<HTMLButtonElement | null>>([]);
  const visibleStrategies =
    capabilities && capabilities.length > 0
      ? strategies.filter(strategy =>
          capabilities.some(item => item.ruleType === strategy.code)
        )
      : strategies;

  function selectRelative(index: number, direction: 1 | -1) {
    const nextIndex =
      (index + direction + visibleStrategies.length) % visibleStrategies.length;
    const next = visibleStrategies[nextIndex];
    if (!next) return;
    onChange(next.code);
    refs.current[nextIndex]?.focus();
  }

  return (
    <div aria-label="选择买入策略" className="grid gap-2" role="radiogroup">
      {visibleStrategies.map((strategy, index) => {
        const capability = capabilities?.find(
          item => item.ruleType === strategy.code
        );
        const Icon = strategy.icon;
        const selected = value === strategy.code;
        return (
          <button
            aria-checked={selected}
            className={cn(
              'min-h-11 cursor-pointer rounded-lg border p-3 text-left transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
              selected
                ? 'border-primary/50 bg-primary/[0.08]'
                : 'border-white/10 bg-white/[0.025] hover:border-primary/30 hover:bg-white/[0.045]'
            )}
            key={strategy.code}
            onClick={() => onChange(strategy.code)}
            onKeyDown={event => {
              if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
                event.preventDefault();
                selectRelative(index, 1);
              }
              if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
                event.preventDefault();
                selectRelative(index, -1);
              }
            }}
            ref={node => {
              refs.current[index] = node;
            }}
            role="radio"
            tabIndex={selected ? 0 : -1}
            type="button"
          >
            <span className="flex items-start gap-3">
              <span className="rounded-md border border-emerald-400/20 bg-emerald-400/10 p-2 text-emerald-200">
                <Icon aria-hidden="true" className="h-4 w-4" />
              </span>
              <span className="min-w-0">
                <span className="flex flex-wrap items-center gap-2 text-ui-body font-black text-slate-100">
                  {capability?.label ?? strategy.title}
                  {strategy.recommended ? (
                    <span className="rounded border border-cyan-400/25 bg-cyan-400/10 px-1.5 py-0.5 text-ui-caption text-cyan-100">
                      推荐
                    </span>
                  ) : null}
                </span>
                <span className="mt-1 block text-ui-label leading-5 text-slate-300">
                  {capability?.description ?? strategy.description}
                </span>
                <span className="mt-2 grid gap-1 text-ui-caption leading-4 text-slate-400 sm:grid-cols-2">
                  <span>
                    适合：{capability?.suitableFor ?? strategy.suitableFor}
                  </span>
                  <span>系统自动：{strategy.automation}</span>
                  <span className="sm:col-span-2">
                    你仍需决定：{strategy.userDecision}
                  </span>
                  {capability?.warning ? (
                    <span className="sm:col-span-2 text-amber-200/80">
                      风险提示：{capability.warning}
                    </span>
                  ) : null}
                </span>
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
