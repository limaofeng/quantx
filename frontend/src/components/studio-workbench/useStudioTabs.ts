import { useCallback, useEffect, useMemo, useState } from 'react';
import type { MouseEvent } from 'react';

import type { StudioTab } from './types';

export interface UseStudioTabsResult<T extends StudioTab> {
  activeTab: T | undefined;
  activeTabId: string | null;
  closeTab: (tabId: string, event?: MouseEvent) => void;
  openPinnedTab: (tab: T) => void;
  openPreviewTab: (tab: T) => void;
  pinTab: (tabId: string) => void;
  replaceTab: (tabId: string, nextTab: T) => void;
  setActiveTabId: (tabId: string | null) => void;
  tabs: T[];
  updateTab: (tabId: string, patch: Partial<T>) => void;
  updateTabDirty: (tabId: string, isDirty: boolean) => void;
}

export function useStudioTabs<T extends StudioTab>(): UseStudioTabsResult<T> {
  const [tabs, setTabs] = useState<T[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);

  const openTab = useCallback((tab: T, isPreview: boolean) => {
    setTabs(currentTabs => {
      const existingTab = currentTabs.find(item => item.id === tab.id);
      if (existingTab) return currentTabs;

      if (isPreview) {
        const previewTab = currentTabs.find(item => item.isPreview);
        if (previewTab) {
          return currentTabs.map(item =>
            item.id === previewTab.id
              ? ({ ...tab, isDirty: false, isPreview: true } as T)
              : item
          );
        }
      }

      return [...currentTabs, { ...tab, isPreview } as T];
    });
    setActiveTabId(tab.id);
  }, []);

  const openPreviewTab = useCallback(
    (tab: T) => {
      openTab(tab, true);
    },
    [openTab]
  );

  const openPinnedTab = useCallback(
    (tab: T) => {
      openTab(tab, false);
    },
    [openTab]
  );

  const closeTab = useCallback((tabId: string, event?: MouseEvent) => {
    event?.stopPropagation();
    setTabs(currentTabs => currentTabs.filter(tab => tab.id !== tabId));
    setActiveTabId(currentActiveTabId =>
      currentActiveTabId === tabId ? null : currentActiveTabId
    );
  }, []);

  useEffect(() => {
    if (activeTabId === null && tabs.length > 0) {
      setActiveTabId(tabs[tabs.length - 1].id);
    }
  }, [activeTabId, tabs]);

  const pinTab = useCallback((tabId: string) => {
    setTabs(currentTabs =>
      currentTabs.map(tab =>
        tab.id === tabId ? ({ ...tab, isPreview: false } as T) : tab
      )
    );
  }, []);

  const updateTab = useCallback((tabId: string, patch: Partial<T>) => {
    setTabs(currentTabs =>
      currentTabs.map(tab => (tab.id === tabId ? ({ ...tab, ...patch } as T) : tab))
    );
  }, []);

  const updateTabDirty = useCallback((tabId: string, isDirty: boolean) => {
    setTabs(currentTabs =>
      currentTabs.map(tab => {
        if (tab.id !== tabId) return tab;
        return { ...tab, isDirty, isPreview: isDirty ? false : tab.isPreview } as T;
      })
    );
  }, []);

  const replaceTab = useCallback((tabId: string, nextTab: T) => {
    setTabs(currentTabs =>
      currentTabs.map(tab =>
        tab.id === tabId ? ({ ...nextTab, isDirty: false } as T) : tab
      )
    );
    setActiveTabId(nextTab.id);
  }, []);

  const activeTab = useMemo(
    () => tabs.find(tab => tab.id === activeTabId),
    [activeTabId, tabs]
  );

  return {
    activeTab,
    activeTabId,
    closeTab,
    openPinnedTab,
    openPreviewTab,
    pinTab,
    replaceTab,
    setActiveTabId,
    tabs,
    updateTab,
    updateTabDirty,
  };
}
