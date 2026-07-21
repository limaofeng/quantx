import {
  Bot,
  Briefcase,
  Database,
  Filter,
  Hand,
  LayoutDashboard,
  LineChart,
  Wallet,
  type LucideIcon,
} from 'lucide-react';

import type { StudioTab } from '@/components/studio-workbench';
import { findRoute, getPageTitle, normalizePath } from '@/router';

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

export function getStudioWorkspacePath(rawPath: string) {
  const { pathname, search } = splitPath(rawPath);
  const normalizedPath = normalizePath(pathname);

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

export function isStudioWorkspacePath(rawPath: string) {
  const path = normalizePath(rawPath);
  const isStrategyStudioRoute =
    path === '/strategies' ||
    path === '/strategies/run' ||
    /^\/strategies\/[^/]+\/run$/.test(path) ||
    /^\/strategies\/(?!run(?:\/|$))[^/]+(?:\/runs\/[^/]+)?$/.test(path);

  return (
    path === '/' ||
    path === '/market-shortcuts' ||
    path === '/holdings' ||
    path === '/account' ||
    path === '/t-trade' ||
    path === '/liquidation' ||
    path === '/screening' ||
    path.startsWith('/stock/') ||
    path === '/settings/data' ||
    path.startsWith('/settings/data/') ||
    path.startsWith('/system/flow-runs/') ||
    isStrategyStudioRoute
  );
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
  if (pathname === '/') return LayoutDashboard;
  if (pathname === '/holdings') return Briefcase;
  if (pathname === '/account') return Wallet;
  if (pathname === '/liquidation') return Hand;
  if (pathname === '/screening') return Filter;
  if (pathname.startsWith('/stock/')) return LineChart;
  if (pathname.startsWith('/strategies')) return Bot;
  if (
    pathname.startsWith('/settings/data') ||
    pathname.startsWith('/system/flow-runs')
  ) {
    return Database;
  }
  return LayoutDashboard;
}

function getTabTitle(pathname: string, search = '') {
  if (pathname === '/holdings') return '持仓';
  if (pathname === '/account') return '账户中心';

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

  return getPageTitle(pathname);
}

export function buildStudioWorkspaceTab(
  rawPath: string
): StudioWorkspaceTab | null {
  const fullPath = getStudioWorkspacePath(rawPath);
  if (!isStudioWorkspacePath(fullPath)) return null;

  const { pathname, search } = splitPath(fullPath);
  const normalizedPath = normalizePath(pathname);
  const path = search ? `${normalizedPath}?${search}` : normalizedPath;

  return {
    icon: getTabIcon(normalizedPath),
    id: getStudioWorkspaceTabId(path),
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
  if (existingIndex === -1) return [...tabs, tab];

  const nextTabs = [...tabs];
  nextTabs[existingIndex] = {
    ...nextTabs[existingIndex],
    ...tab,
  };
  return nextTabs;
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
  if (!fallback) return tab;
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
    .reduce<
      StudioWorkspaceTab[]
    >((mergedTabs, tab) => mergeStudioWorkspaceTab(mergedTabs, tab), []);
}
