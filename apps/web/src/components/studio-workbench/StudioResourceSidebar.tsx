import { RefreshCw, Search, type LucideIcon } from 'lucide-react';
import type { MouseEvent, ReactNode } from 'react';

import { Input } from '@/components/ui/input';
import { cn } from '@/utils/cn';

export interface StudioResourceSidebarItem<TId extends string = string> {
  description: string;
  icon: LucideIcon;
  id: TId;
  label: string;
}

interface StudioResourceSidebarProps<
  TId extends string,
  TItem extends StudioResourceSidebarItem<TId>,
> {
  activeId: TId;
  description?: string;
  emptyLabel?: string;
  eyebrow: string;
  footerActionLabel?: string;
  headerExtra?: ReactNode;
  items: TItem[];
  listExtra?: ReactNode;
  onFooterAction?: () => void;
  onItemContextMenu?: (
    event: MouseEvent<HTMLButtonElement>,
    item: TItem
  ) => void;
  onItemSelect: (id: TId) => void;
  onSearchChange?: (value: string) => void;
  searchPlaceholder?: string;
  searchValue?: string;
  sectionLabel?: string;
}

export function StudioResourceSidebar<
  TId extends string,
  TItem extends StudioResourceSidebarItem<TId>,
>({
  activeId,
  description,
  emptyLabel = '没有匹配资源',
  eyebrow,
  footerActionLabel,
  headerExtra,
  items,
  listExtra,
  onFooterAction,
  onItemContextMenu,
  onItemSelect,
  onSearchChange,
  searchPlaceholder = '搜索资源',
  searchValue = '',
  sectionLabel = 'Resources',
}: StudioResourceSidebarProps<TId, TItem>) {
  const hasSearch = Boolean(onSearchChange);

  return (
    <aside className="flex h-full min-h-0 flex-col">
      <div className="border-b border-white/5 p-ui-panel">
        <div className="text-ui-caption font-bold uppercase tracking-[0.2em] text-blue-400">
          {eyebrow}
        </div>
        {description && (
          <div className="mt-1 text-ui-label font-medium text-slate-500">
            {description}
          </div>
        )}
        {headerExtra}
        {hasSearch && (
          <label className="mt-3 flex h-control-compact items-center gap-2 rounded-control border border-white/10 bg-white/[0.03] px-2 text-slate-500 transition-colors focus-within:border-blue-500/40">
            <Search className="h-3.5 w-3.5 shrink-0" />
            <span className="sr-only">{searchPlaceholder}</span>
            <Input
              value={searchValue}
              onChange={event => onSearchChange?.(event.target.value)}
              placeholder={searchPlaceholder}
              className="h-full min-w-0 flex-1 border-0 bg-transparent px-0 text-ui-label font-medium text-slate-200 shadow-none outline-none placeholder:text-slate-600 hover:border-0 focus-visible:border-0 focus-visible:ring-0"
            />
          </label>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2 custom-scrollbar">
        <div className="mb-2 px-2 text-ui-caption font-bold uppercase tracking-[0.18em] text-slate-600">
          {sectionLabel}
        </div>
        <div className="space-y-1">
          {items.map(item => {
            const Icon = item.icon;
            const isActive = item.id === activeId;

            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onItemSelect(item.id)}
                onContextMenu={event => onItemContextMenu?.(event, item)}
                className={cn(
                  'flex min-h-control-large w-full cursor-pointer items-center gap-2.5 rounded-control border px-2.5 py-1.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70',
                  isActive
                    ? 'border-blue-500/30 bg-blue-500/10 text-blue-100'
                    : 'border-transparent text-slate-400 hover:border-white/10 hover:bg-white/[0.04] hover:text-slate-200'
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-ui-label font-semibold">
                    {item.label}
                  </span>
                  <span className="block truncate text-ui-caption text-slate-600">
                    {item.description}
                  </span>
                </span>
              </button>
            );
          })}
          {items.length === 0 && (
            <div className="px-2 py-ui-empty text-center text-ui-caption text-slate-600">
              {emptyLabel}
            </div>
          )}
        </div>

        {listExtra && (
          <div className="mt-3 border-t border-white/5 pt-3">{listExtra}</div>
        )}
      </div>

      {footerActionLabel && onFooterAction && (
        <div className="border-t border-white/5 p-ui-panel">
          <button
            type="button"
            onClick={onFooterAction}
            className="flex h-control-compact w-full cursor-pointer items-center justify-center gap-2 rounded-control border border-white/10 text-ui-caption font-semibold uppercase tracking-wider text-slate-400 transition-colors hover:border-blue-500/40 hover:text-blue-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            {footerActionLabel}
          </button>
        </div>
      )}
    </aside>
  );
}
