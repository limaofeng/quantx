import { ChevronDown, Pin, Plus, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type React from 'react';

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/utils/cn';

import {
  StudioTabContextMenu,
  type StudioTabContextMenuAction,
  type StudioTabContextMenuState,
} from './StudioTabContextMenu';
import { getStudioThemeStyles } from './themeStyles';
import type { StudioTab, StudioThemeName } from './types';

const MAX_VISIBLE_TABS = 8;
const TAB_SLOT_WIDTH = 148;
const TAB_BAR_RESERVED_WIDTH = 52;

export interface TabBarProps<T extends StudioTab> {
  activeTabId: string | null;
  closable?: boolean;
  createTooltip?: string;
  onTabChange: (tabId: string) => void;
  onTabClose: (tabId: string, event?: React.MouseEvent) => void;
  onTabCreate?: () => void;
  onTabPin?: (tabId: string, pinned: boolean) => void;
  renderTabContent?: (tab: T, isActive: boolean) => React.ReactNode;
  tabs: T[];
  themeColor: StudioThemeName;
}

function partitionTabs<T extends StudioTab>(
  tabs: T[],
  visibleCapacity: number,
  activeTabId: string | null
) {
  if (tabs.length <= visibleCapacity) {
    return { hiddenTabs: [] as T[], visibleTabs: tabs };
  }

  const initialVisibleTabs = tabs.slice(0, visibleCapacity);
  const activeTab = tabs.find(tab => tab.id === activeTabId);
  const activeIsVisible = initialVisibleTabs.some(
    tab => tab.id === activeTabId
  );
  const visibleTabs =
    activeTab && !activeIsVisible
      ? [...initialVisibleTabs.slice(0, -1), activeTab]
      : initialVisibleTabs;
  const visibleIds = new Set(visibleTabs.map(tab => tab.id));

  return {
    hiddenTabs: tabs.filter(tab => !visibleIds.has(tab.id)),
    visibleTabs,
  };
}

export function TabBar<T extends StudioTab>({
  activeTabId,
  closable = true,
  createTooltip = 'New Tab',
  onTabChange,
  onTabClose,
  onTabCreate,
  onTabPin,
  renderTabContent,
  tabs,
  themeColor,
}: TabBarProps<T>) {
  const [contextMenu, setContextMenu] =
    useState<StudioTabContextMenuState | null>(null);
  const [visibleCapacity, setVisibleCapacity] = useState(MAX_VISIBLE_TABS);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const tabButtonRefs = useRef(new Map<string, HTMLButtonElement>());
  const themeStyles = getStudioThemeStyles(themeColor);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const updateVisibleCapacity = () => {
      const containerWidth = container.getBoundingClientRect().width;
      if (containerWidth <= 0) return;
      const availableWidth = containerWidth - TAB_BAR_RESERVED_WIDTH;
      const nextCapacity = Math.max(
        1,
        Math.min(MAX_VISIBLE_TABS, Math.floor(availableWidth / TAB_SLOT_WIDTH))
      );
      setVisibleCapacity(nextCapacity);
    };

    updateVisibleCapacity();

    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', updateVisibleCapacity);
      return () => window.removeEventListener('resize', updateVisibleCapacity);
    }

    const observer = new ResizeObserver(updateVisibleCapacity);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  const { hiddenTabs, visibleTabs } = useMemo(
    () => partitionTabs(tabs, visibleCapacity, activeTabId),
    [activeTabId, tabs, visibleCapacity]
  );

  const handleContextMenuAction = useCallback(
    (action: StudioTabContextMenuAction, tabId: string) => {
      const tabIndex = tabs.findIndex(tab => tab.id === tabId);
      if (tabIndex === -1) return;

      if (action === 'pin' || action === 'unpin') {
        onTabPin?.(tabId, action === 'pin');
        return;
      }

      if (action === 'close') {
        onTabClose(tabId);
        return;
      }

      if (action === 'closeOthers') {
        onTabChange(tabId);
        tabs.filter(tab => tab.id !== tabId).forEach(tab => onTabClose(tab.id));
        return;
      }

      if (action === 'closeRight') {
        onTabChange(tabId);
        tabs.slice(tabIndex + 1).forEach(tab => onTabClose(tab.id));
        return;
      }

      tabs.forEach(tab => onTabClose(tab.id));
    },
    [onTabChange, onTabClose, onTabPin, tabs]
  );

  const handleTabKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLButtonElement>, tabId: string) => {
      const currentIndex = visibleTabs.findIndex(tab => tab.id === tabId);
      if (currentIndex === -1) return;

      if (event.key === 'Delete' && closable) {
        event.preventDefault();
        onTabClose(tabId);
        return;
      }

      let nextIndex: number | null = null;
      if (event.key === 'ArrowLeft') {
        nextIndex =
          (currentIndex - 1 + visibleTabs.length) % visibleTabs.length;
      } else if (event.key === 'ArrowRight') {
        nextIndex = (currentIndex + 1) % visibleTabs.length;
      } else if (event.key === 'Home') {
        nextIndex = 0;
      } else if (event.key === 'End') {
        nextIndex = visibleTabs.length - 1;
      }

      if (nextIndex === null) return;

      event.preventDefault();
      const nextTab = visibleTabs[nextIndex];
      onTabChange(nextTab.id);
      window.requestAnimationFrame(() => {
        tabButtonRefs.current.get(nextTab.id)?.focus();
      });
    },
    [closable, onTabChange, onTabClose, visibleTabs]
  );

  return (
    <div
      ref={containerRef}
      className="relative h-10 shrink-0 overflow-hidden border-b border-white/5 bg-[#0b1120]/70"
      data-testid="studio-tab-bar"
    >
      <div
        aria-label="工作区标签"
        className="flex h-full min-w-0 items-center gap-1 px-2"
        role="tablist"
      >
        {visibleTabs.map(tab => {
          const isActive = activeTabId === tab.id;

          return (
            <div
              key={tab.id}
              onContextMenu={event => {
                if (!closable) return;
                event.preventDefault();
                event.stopPropagation();
                setContextMenu({
                  isPreview: Boolean(tab.isPreview),
                  isPreviewable: Boolean(tab.isPreviewable),
                  tabId: tab.id,
                  tabIndex: tabs.findIndex(item => item.id === tab.id),
                  x: event.clientX,
                  y: event.clientY,
                });
              }}
              className={cn(
                'group relative flex h-full w-[148px] shrink-0 items-center border-b-2 transition-colors',
                isActive
                  ? themeStyles.activeTab
                  : 'border-transparent bg-transparent text-slate-400 hover:bg-white/5 hover:text-slate-200'
              )}
            >
              <button
                ref={node => {
                  if (node) tabButtonRefs.current.set(tab.id, node);
                  else tabButtonRefs.current.delete(tab.id);
                }}
                type="button"
                role="tab"
                aria-selected={isActive}
                tabIndex={isActive ? 0 : -1}
                data-studio-tab-id={tab.id}
                onClick={() => onTabChange(tab.id)}
                onDoubleClick={() => {
                  if (tab.isPreview) onTabPin?.(tab.id, true);
                }}
                onKeyDown={event => handleTabKeyDown(event, tab.id)}
                title={
                  tab.isPreview ? `${tab.name}（预览标签，双击固定）` : tab.name
                }
                className="flex h-full min-w-0 flex-1 cursor-pointer items-center gap-2 pl-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-red-400/80"
              >
                <span className="flex min-w-0 flex-1 items-center gap-2">
                  {renderTabContent ? (
                    renderTabContent(tab, isActive)
                  ) : (
                    <>
                      {tab.icon && (
                        <tab.icon size={13} className={themeStyles.tabIcon} />
                      )}
                      <span
                        className={cn(
                          'truncate text-xs font-bold',
                          tab.isPreview && 'italic'
                        )}
                      >
                        {tab.name}
                      </span>
                    </>
                  )}
                </span>
                {tab.isPreviewable && !tab.isPreview && (
                  <Pin
                    aria-label="已固定"
                    className="h-3 w-3 shrink-0 text-slate-500"
                  />
                )}
              </button>

              {closable && (
                <div className="relative mr-1 flex h-6 w-6 shrink-0 items-center justify-center">
                  {tab.isDirty && (
                    <span className="pointer-events-none absolute inset-0 flex items-center justify-center transition-opacity group-hover:opacity-0 group-focus-within:opacity-0">
                      <span className="relative flex h-2 w-2">
                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-orange-400 opacity-75" />
                        <span className="relative inline-flex h-2 w-2 rounded-full bg-orange-500" />
                      </span>
                    </span>
                  )}

                  <button
                    type="button"
                    onClick={event => onTabClose(tab.id, event)}
                    className={cn(
                      'rounded-md p-1 text-slate-400 opacity-0 transition-colors hover:bg-white/10 hover:text-white focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400/80 group-hover:opacity-100 group-focus-within:opacity-100',
                      isActive && 'opacity-100'
                    )}
                    aria-label={`关闭 ${tab.name}`}
                  >
                    <X size={13} />
                  </button>
                </div>
              )}
            </div>
          );
        })}

        {hiddenTabs.length > 0 && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className="ml-auto flex h-8 shrink-0 cursor-pointer items-center gap-1 rounded-md border border-white/10 bg-white/[0.03] px-2 text-xs font-bold text-slate-300 transition-colors hover:bg-white/[0.07] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400/80"
                aria-label={`更多标签，${hiddenTabs.length} 个`}
                data-testid="studio-tab-overflow-trigger"
              >
                <span>更多</span>
                <span className="font-mono text-[10px] text-slate-400">
                  {hiddenTabs.length}
                </span>
                <ChevronDown className="h-3.5 w-3.5" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="end"
              className="w-64 border-white/10 bg-[#0b1120] text-slate-200 shadow-xl shadow-black/30"
              sideOffset={0}
            >
              <DropdownMenuLabel className="text-[11px] font-black uppercase tracking-[0.16em] text-slate-400">
                已收纳的标签
              </DropdownMenuLabel>
              <DropdownMenuSeparator className="bg-white/10" />
              {hiddenTabs.map(tab => {
                const Icon = tab.icon;
                return (
                  <DropdownMenuItem
                    key={tab.id}
                    onSelect={() => onTabChange(tab.id)}
                    className="cursor-pointer py-2 text-xs focus:bg-white/[0.07] focus:text-white"
                  >
                    {Icon && <Icon className="h-3.5 w-3.5 text-slate-400" />}
                    <span
                      className={cn(
                        'min-w-0 flex-1 truncate',
                        tab.isPreview && 'italic'
                      )}
                    >
                      {tab.name}
                    </span>
                    {tab.isPreviewable && !tab.isPreview && (
                      <Pin className="h-3 w-3 text-slate-500" />
                    )}
                  </DropdownMenuItem>
                );
              })}
            </DropdownMenuContent>
          </DropdownMenu>
        )}

        {onTabCreate && (
          <button
            type="button"
            onClick={onTabCreate}
            className="flex h-full shrink-0 cursor-pointer items-center justify-center px-3 text-slate-400 transition-colors hover:bg-white/5 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-red-400/80"
            title={createTooltip}
          >
            <Plus size={16} strokeWidth={2} />
          </button>
        )}
      </div>

      <StudioTabContextMenu
        menu={closable ? contextMenu : null}
        onAction={handleContextMenuAction}
        onClose={() => setContextMenu(null)}
        tabCount={tabs.length}
      />
    </div>
  );
}
