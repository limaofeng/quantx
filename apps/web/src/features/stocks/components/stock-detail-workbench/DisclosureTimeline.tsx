import { ExternalLink, FileText } from 'lucide-react';
import * as React from 'react';

import type { StockDisclosureSummaryQuery as StockDisclosureSummaryData } from '@/generated/gql/graphql';
import { cn } from '@/utils/cn';

import { formatDate, sourceLabel } from './formatters';

type DisclosureSummary = NonNullable<
  StockDisclosureSummaryData['stockDisclosureSummary']
>;
type Announcement = DisclosureSummary['announcements'][0];

type DisclosureFilter =
  'all' | 'repurchase' | 'financial' | 'major' | 'risk' | 'holdingChange';

const FILTERS: { id: DisclosureFilter; label: string }[] = [
  { id: 'all', label: '全部' },
  { id: 'repurchase', label: '回购' },
  { id: 'financial', label: '财务报告' },
  { id: 'major', label: '重大事项' },
  { id: 'risk', label: '风险提示' },
  { id: 'holdingChange', label: '持股变动' },
];

function matchesFilter(item: Announcement, filter: DisclosureFilter) {
  if (filter === 'all') return true;
  const text = `${item.title || ''} ${item.announcementType || ''}`;
  if (filter === 'repurchase')
    return item.isRepurchaseRelated || text.includes('回购');
  if (filter === 'financial')
    return text.includes('财务') || text.includes('报告');
  if (filter === 'major') return text.includes('重大');
  if (filter === 'risk') return text.includes('风险');
  return text.includes('持股') || text.includes('股权变动');
}

function renderHighlightedTitle(title: string) {
  const parts = title.split(/(回购)/g);
  return parts.map((part, index) =>
    part === '回购' ? (
      <span key={`${part}-${index}`} className="text-red-200">
        {part}
      </span>
    ) : (
      <React.Fragment key={`${part}-${index}`}>{part}</React.Fragment>
    )
  );
}

export function DisclosureTimeline({
  announcements,
  isLoading,
  sourceStatus,
}: {
  announcements: Announcement[];
  isLoading: boolean;
  sourceStatus?: string | null;
}) {
  const [activeFilter, setActiveFilter] =
    React.useState<DisclosureFilter>('all');
  const filteredItems = React.useMemo(
    () => announcements.filter(item => matchesFilter(item, activeFilter)),
    [activeFilter, announcements]
  );

  return (
    <section className="min-w-0 border border-white/5 bg-[#0b1120]/70">
      <div className="flex min-h-10 flex-col gap-2 border-b border-white/5 px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-2">
          <FileText className="h-3.5 w-3.5 text-blue-300" />
          <h3 className="truncate text-xs font-black text-slate-200">
            公告动态
          </h3>
          <span className="rounded border border-white/10 px-2 py-0.5 font-mono text-[10px] font-bold text-slate-500">
            {announcements.length}
          </span>
        </div>
        <div className="flex max-w-full flex-wrap gap-1 sm:justify-end">
          {FILTERS.map(filter => (
            <button
              key={filter.id}
              type="button"
              onClick={() => setActiveFilter(filter.id)}
              className={cn(
                'h-7 shrink-0 rounded-md px-2.5 text-[10px] font-black transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70',
                activeFilter === filter.id
                  ? 'bg-blue-500/15 text-blue-100'
                  : 'text-slate-500 hover:bg-white/[0.05] hover:text-slate-200'
              )}
            >
              {filter.label}
            </button>
          ))}
        </div>
      </div>

      <div className="max-h-[520px] overflow-y-auto p-3 custom-scrollbar">
        {isLoading ? (
          <div className="flex h-32 items-center justify-center text-xs font-bold text-slate-500">
            公告读取中
          </div>
        ) : filteredItems.length === 0 ? (
          <div className="flex h-32 flex-col items-center justify-center gap-2 text-center">
            <div className="text-xs font-bold text-slate-400">暂无匹配公告</div>
            <div className="max-w-sm text-[11px] font-medium text-slate-600">
              {sourceStatus === 'READY'
                ? '等待首次同步，可点击刷新公告。'
                : '可尝试刷新，或调整公告分类筛选。'}
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {filteredItems.map(item => (
              <article
                key={item.id}
                className="grid min-w-0 gap-2 border border-white/5 bg-[#08101d]/80 p-3 md:grid-cols-[96px_minmax(0,1fr)_auto]"
              >
                <div className="font-mono text-[11px] font-black text-slate-500">
                  {formatDate(item.announceDate)}
                </div>
                <div className="min-w-0">
                  <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                    <span
                      className={cn(
                        'rounded border px-1.5 py-0.5 text-[10px] font-black',
                        item.isRepurchaseRelated
                          ? 'border-red-500/25 bg-red-500/10 text-red-200'
                          : 'border-white/10 bg-white/[0.04] text-slate-400'
                      )}
                    >
                      {item.announcementType ||
                        (item.isRepurchaseRelated ? '回购' : '公告')}
                    </span>
                    <span className="text-[10px] font-bold text-slate-600">
                      {sourceLabel(item.source)}
                    </span>
                  </div>
                  <h4 className="mt-1 line-clamp-2 text-xs font-bold leading-5 text-slate-200">
                    {renderHighlightedTitle(item.title)}
                  </h4>
                </div>
                {item.sourceUrl ? (
                  <a
                    href={item.sourceUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-blue-500/10 hover:text-blue-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
                    aria-label="打开公告原文"
                    title="打开公告原文"
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                ) : (
                  <span className="h-8 w-8" />
                )}
              </article>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
