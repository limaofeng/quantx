import { Plus, X } from 'lucide-react';
import { useCallback, useState } from 'react';
import type React from 'react';

import { cn } from '@/utils/cn';

import {
  StudioTabContextMenu,
  type StudioTabContextMenuAction,
  type StudioTabContextMenuState,
} from './StudioTabContextMenu';
import { getStudioThemeStyles } from './themeStyles';
import type { StudioTab, StudioThemeName } from './types';

export interface TabBarProps<T extends StudioTab> {
  activeTabId: string | null;
  closable?: boolean;
  createTooltip?: string;
  onTabChange: (tabId: string) => void;
  onTabClose: (tabId: string, event?: React.MouseEvent) => void;
  onTabCreate?: () => void;
  renderTabContent?: (tab: T, isActive: boolean) => React.ReactNode;
  tabs: T[];
  themeColor: StudioThemeName;
}

export function TabBar<T extends StudioTab>({
  activeTabId,
  closable = true,
  createTooltip = 'New Tab',
  onTabChange,
  onTabClose,
  onTabCreate,
  renderTabContent,
  tabs,
  themeColor,
}: TabBarProps<T>) {
  const [contextMenu, setContextMenu] =
    useState<StudioTabContextMenuState | null>(null);
  const themeStyles = getStudioThemeStyles(themeColor);

  const handleContextMenuAction = useCallback(
    (action: StudioTabContextMenuAction, tabId: string) => {
      const tabIndex = tabs.findIndex(tab => tab.id === tabId);
      if (tabIndex === -1) return;

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
    [onTabChange, onTabClose, tabs]
  );

  return (
    <div className="relative h-10 shrink-0 overflow-hidden border-b border-white/5 bg-[#0b1120]/70">
      <div className="flex h-full items-center gap-1 overflow-x-auto overflow-y-hidden px-2 no-scrollbar">
        {tabs.map((tab, tabIndex) => {
          const isActive = activeTabId === tab.id;

          return (
            <div
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              onContextMenu={event => {
                if (!closable) return;
                event.preventDefault();
                event.stopPropagation();
                setContextMenu({
                  tabId: tab.id,
                  tabIndex,
                  x: event.clientX,
                  y: event.clientY,
                });
              }}
              className={cn(
                'group relative flex h-full min-w-[120px] shrink-0 cursor-pointer items-center gap-2 border-b-2 pl-4 transition-colors',
                closable ? 'pr-1.5' : 'pr-4',
                isActive
                  ? themeStyles.activeTab
                  : 'border-transparent bg-transparent text-slate-400 hover:bg-white/5 hover:text-slate-300'
              )}
            >
              {renderTabContent ? (
                renderTabContent(tab, isActive)
              ) : (
                <>
                  {tab.icon && (
                    <tab.icon size={12} className={themeStyles.tabIcon} />
                  )}
                  <span
                    className={cn(
                      'max-w-[120px] truncate text-[11px] font-bold',
                      tab.isPreview && 'pr-1 italic'
                    )}
                  >
                    {tab.name}
                  </span>
                </>
              )}

              {closable && (
                <div className="relative ml-auto flex h-5 w-5 items-center justify-center">
                  {tab.isDirty && (
                    <span className="pointer-events-none absolute inset-0 flex items-center justify-center transition-opacity group-hover:opacity-0">
                      <span className="relative flex h-2 w-2">
                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-orange-400 opacity-75" />
                        <span className="relative inline-flex h-2 w-2 rounded-full bg-orange-500" />
                      </span>
                    </span>
                  )}

                  <button
                    type="button"
                    onClick={event => onTabClose(tab.id, event)}
                    className="rounded-md p-1 text-slate-400 opacity-0 transition-colors hover:bg-white/10 hover:text-white group-hover:opacity-100"
                    aria-label={`关闭 ${tab.name}`}
                  >
                    <X size={12} />
                  </button>
                </div>
              )}
            </div>
          );
        })}

        {onTabCreate && (
          <button
            type="button"
            onClick={onTabCreate}
            className="flex h-full shrink-0 items-center justify-center px-3 text-slate-500 transition-colors hover:bg-white/5 hover:text-white"
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
