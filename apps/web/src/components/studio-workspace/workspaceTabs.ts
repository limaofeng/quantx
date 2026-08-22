import {
  LayoutDashboard,
  LineChart,
  Wallet,
  type LucideIcon,
} from 'lucide-react';

import type { StudioTab } from '@/components/studio-workbench';
import {
  ControlSettingsIcon,
  MarketDataIcon,
  MarketResearchIcon,
  PortfolioHoldingsIcon,
  SellManagementIcon,
  StockScreeningIcon,
  StrategyGraphIcon,
} from '@/components/studio-workspace/StudioNavigationIcons';
import {
  findRoute,
  getPageTitle,
  normalizePath,
  safeDecodeURIComponent,
} from '@/router';

export interface StudioWorkspaceTab extends StudioTab {
  icon: LucideIcon;
  path: string;
}

interface SplitPath {
  pathname: string;
  search: string;
}

function splitPath(rawPath: string): SplitPath {
  const fallbackPath = rawPath || '/';
  const hashlessPath = fallbackPath.split('#')[0] || '/';
  const queryIndex = hashlessPath.indexOf('?');

  if (queryIndex === -1) {
    return {
      pathname: hashlessPath || '/',
      search: '',
    };
  }

  return {
    pathname: hashlessPath.slice(0, queryIndex) || '/',
    search: hashlessPath.slice(queryIndex + 1),
  };
}

function normalizeSymbol(value: string | null | undefined) {
  return (value || '').trim().toUpperCase();
}

function getStockCode(pathname: string) {
  const parts = normalizePath(pathname).split('/').filter(Boolean);
  return normalizeSymbol(parts[1]);
}

function getSearchParam(search: string, key: string) {
  return new URLSearchParams(search).get(key);
}

function isPreviewablePath(pathname: string) {
  return (
    pathname.startsWith('/stock/') ||
    pathname.startsWith('/research/') ||
    pathname.startsWith('/system/flow-runs/')
  );
}

export function getStudioWorkspacePath(rawPath: string) {
  const { pathname, search } = splitPath(rawPath);
  const normalizedPath =
    normalizePath(pathname) === '/market-shortcuts'
      ? '/'
      : normalizePath(pathname);

  if (search) return `${normalizedPath}?${search}`;

  if (
    typeof window !== 'undefined' &&
    normalizePath(window.location.pathname) === normalizedPath &&
    window.location.search
  ) {
    return `${normalizedPath}${window.location.search}`;
  }

  return normalizedPath;
}

export function getStudioWorkspaceTabId(rawPath: string) {
  const { pathname, search } = splitPath(getStudioWorkspacePath(rawPath));
  const normalizedPath = normalizePath(pathname);

  if (normalizedPath === '/holdings') {
    return 'holdings';
  }

  if (normalizedPath === '/account') {
    return 'account';
  }

  if (
    [
      '/settings',
      '/settings/qmt',
      '/settings/ai-runtime',
      '/settings/agents',
    ].includes(normalizedPath)
  ) {
    return 'settings';
  }

  if (normalizedPath === '/liquidation') {
    const manualTabId = normalizeSymbol(getSearchParam(search, 'workspaceTab'));
    return manualTabId ? `liquidation:${manualTabId}` : 'liquidation';
  }

  if (normalizedPath.startsWith('/stock/')) {
    return `stock:${getStockCode(normalizedPath)}`;
  }

  return search ? `page:${normalizedPath}?${search}` : `page:${normalizedPath}`;
}

function getTabIcon(pathname: string): LucideIcon {
  const route = findRoute(pathname);
  if (route?.nav?.icon) return route.nav.icon;
  if (pathname === '/holdings') return PortfolioHoldingsIcon;
  if (pathname === '/account') return Wallet;
  if (pathname === '/liquidation') return SellManagementIcon;
  if (pathname === '/screening') return StockScreeningIcon;
  if (pathname.startsWith('/research')) return MarketResearchIcon;
  if (pathname.startsWith('/stock/')) return LineChart;
  if (pathname.startsWith('/strategies')) return StrategyGraphIcon;
  if (
    [
      '/settings',
      '/settings/qmt',
      '/settings/ai-runtime',
      '/settings/agents',
    ].includes(pathname)
  ) {
    return ControlSettingsIcon;
  }
  if (
    pathname.startsWith('/settings/data') ||
    pathname.startsWith('/system/flow-runs')
  ) {
    return MarketDataIcon;
  }
  return LayoutDashboard;
}

function getTabTitle(pathname: string, search = '') {
  if (pathname === '/screening') return '自选股';
  if (pathname === '/holdings') return '持仓';
  if (pathname === '/account') return '账户概览';
  if (
    [
      '/settings',
      '/settings/qmt',
      '/settings/ai-runtime',
      '/settings/agents',
    ].includes(pathname)
  ) {
    return '系统设置';
  }

  if (pathname === '/liquidation' && getSearchParam(search, 'workspaceTab')) {
    const stockName = (getSearchParam(search, 'name') || '').trim();
    const stockCode = normalizeSymbol(getSearchParam(search, 'symbol'));
    const displayName = stockName || stockCode;
    return displayName ? `清仓 ${displayName}` : getPageTitle(pathname);
  }

  if (pathname.startsWith('/stock/')) {
    const stockCode = getStockCode(pathname);
    return stockCode ? `个股 ${stockCode}` : '个股详情';
  }

  if (pathname.startsWith('/research/')) {
    const parts = normalizePath(pathname).split('/').filter(Boolean);
    const encodedRunId = parts[4];
    if (!encodedRunId) return '研究详情';
    const runId = safeDecodeURIComponent(encodedRunId);
    const suffix = runId.length > 12 ? runId.slice(-12) : runId;
    return `研究 ${suffix}`;
  }

  return findRoute(pathname) ? getPageTitle(pathname) : '页面未找到';
}

export function buildStudioWorkspaceTab(rawPath: string): StudioWorkspaceTab {
  const fullPath = getStudioWorkspacePath(rawPath);
  const { pathname, search } = splitPath(fullPath);
  const normalizedPath = normalizePath(pathname);
  const path = search ? `${normalizedPath}?${search}` : normalizedPath;
  const isPreviewable = isPreviewablePath(normalizedPath);

  return {
    icon: getTabIcon(normalizedPath),
    id: getStudioWorkspaceTabId(path),
    isPreview: isPreviewable,
    isPreviewable,
    name: getTabTitle(normalizedPath, search),
    path,
    type: 'studio-workspace',
  };
}

export function mergeStudioWorkspaceTab(
  tabs: StudioWorkspaceTab[],
  tab: StudioWorkspaceTab
) {
  const existingIndex = tabs.findIndex(item => item.id === tab.id);
  if (existingIndex === -1) {
    if (tab.isPreview) {
      const previewIndex = tabs.findIndex(
        item => item.isPreview && !item.isDirty
      );
      if (previewIndex !== -1) {
        const nextTabs = [...tabs];
        nextTabs[previewIndex] = tab;
        return nextTabs;
      }
    }

    return [...tabs, tab];
  }

  const nextTabs = [...tabs];
  const existingTab = nextTabs[existingIndex];
  nextTabs[existingIndex] = {
    ...existingTab,
    ...tab,
    isPreview:
      existingTab.isPreviewable && existingTab.isPreview === false
        ? false
        : tab.isPreview,
  };
  return nextTabs;
}

export function setStudioWorkspaceTabPinned(
  tabs: StudioWorkspaceTab[],
  tabId: string,
  pinned: boolean
) {
  return tabs.map(tab =>
    tab.id === tabId && tab.isPreviewable
      ? {
          ...tab,
          isPreview: !pinned,
        }
      : tab
  );
}

function isSafeTabName(tab: StudioWorkspaceTab, fallback: StudioWorkspaceTab) {
  if (tab.id === 'holdings' || tab.id === 'account') {
    return tab.name === fallback.name;
  }

  return tab.name === fallback.name;
}

export function normalizeStudioWorkspaceTabTitle(
  tab: StudioWorkspaceTab
): StudioWorkspaceTab {
  const fallback = buildStudioWorkspaceTab(tab.path);
  if (isSafeTabName(tab, fallback)) return tab;

  return {
    ...tab,
    icon: fallback.icon,
    id: fallback.id,
    name: fallback.name,
    path: fallback.path,
    type: fallback.type,
  };
}

export function normalizeStudioWorkspaceTabTitles(tabs: StudioWorkspaceTab[]) {
  return tabs.map(tab => normalizeStudioWorkspaceTabTitle(tab));
}

export function normalizeStudioWorkspaceTabs(tabs: StudioWorkspaceTab[]) {
  return tabs
    .map(tab => normalizeStudioWorkspaceTabTitle(tab))
    .reduce<StudioWorkspaceTab[]>(
      (mergedTabs, tab) => mergeStudioWorkspaceTab(mergedTabs, tab),
      []
    );
}
