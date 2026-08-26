import * as React from 'react';

import { cn } from '@/utils/cn';

import type { EntryPlanTab } from '../model/types';

const tabs: Array<{ id: EntryPlanTab; label: string }> = [
  { id: 'PLANS', label: '建仓/加仓计划' },
  { id: 'PENDING', label: '待确认买入' },
  { id: 'HISTORY', label: '买入记录' },
];

export function EntryPlanModeTabs({
  activeTab,
  onChange,
  pendingCount,
}: {
  activeTab: EntryPlanTab;
  onChange: (tab: EntryPlanTab) => void;
  pendingCount: number;
}) {
  const buttonRefs = React.useRef<Array<HTMLButtonElement | null>>([]);

  function moveFocus(index: number, direction: 1 | -1) {
    const nextIndex = (index + direction + tabs.length) % tabs.length;
    const nextTab = tabs[nextIndex];
    if (!nextTab) return;
    onChange(nextTab.id);
    buttonRefs.current[nextIndex]?.focus();
  }

  return (
    <div
      aria-label="买入管理视图"
      className="flex min-w-max gap-1"
      role="tablist"
    >
      {tabs.map((tab, index) => (
        <button
          aria-controls={`entry-plan-panel-${tab.id}`}
          aria-selected={activeTab === tab.id}
          className={cn(
            'min-h-11 cursor-pointer rounded-lg border px-3 text-ui-label font-black transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
            activeTab === tab.id
              ? 'border-primary/40 bg-primary/10 text-primary'
              : 'border-transparent text-slate-400 hover:border-white/10 hover:bg-white/[0.04] hover:text-slate-100'
          )}
          id={`entry-plan-tab-${tab.id}`}
          key={tab.id}
          onClick={() => onChange(tab.id)}
          onKeyDown={event => {
            if (event.key === 'ArrowRight') {
              event.preventDefault();
              moveFocus(index, 1);
            }
            if (event.key === 'ArrowLeft') {
              event.preventDefault();
              moveFocus(index, -1);
            }
          }}
          ref={node => {
            buttonRefs.current[index] = node;
          }}
          role="tab"
          tabIndex={activeTab === tab.id ? 0 : -1}
          type="button"
        >
          {tab.label}
          {tab.id === 'PENDING' && pendingCount > 0 ? (
            <span className="ml-2 rounded-full bg-amber-400/20 px-1.5 py-0.5 font-mono text-ui-caption text-amber-100">
              {pendingCount}
            </span>
          ) : null}
        </button>
      ))}
    </div>
  );
}
