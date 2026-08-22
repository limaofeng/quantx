import { type LucideIcon } from 'lucide-react';

import {
  BuyManagementIcon,
  ControlSettingsIcon,
  LimitUpBoardIcon,
  MarketResearchIcon,
  MarketWorkbenchIcon,
  PortfolioHoldingsIcon,
  SellManagementIcon,
  StockScreeningIcon,
  StrategyGraphIcon,
  TTradeCycleIcon,
} from '@/components/studio-workspace/StudioNavigationIcons';

import {
  createLazyRoute,
  preloadImporter,
  type RouteComponent,
  type RouteImporter,
} from './lazy';
import type { RouteSkeletonVariant } from './skeletons';

export interface RouteNavConfig {
  label: string;
  icon: LucideIcon;
  group?: string;
  order?: number;
}

export interface AppRouteConfig {
  path: string;
  title: string | ((pathname: string) => string);
  component: RouteComponent;
  importer: RouteImporter;
  skeleton?: RouteSkeletonVariant;
  nav?: RouteNavConfig;
  preload?: boolean;
}

export interface NavigationItem {
  label: string;
  href: string;
  icon: LucideIcon;
  order: number;
}

export interface NavigationGroup {
  title: string;
  items: NavigationItem[];
}

type RouteDefinition = Omit<AppRouteConfig, 'component'>;

const MAIN_GROUP = '主菜单';
const SETTINGS_GROUP = '系统设置';

function toDefaultExport<T extends Record<string, unknown>>(
  importModule: () => Promise<T>,
  key: keyof T
): RouteImporter {
  return () =>
    importModule().then(module => ({
      default: module[key] as RouteComponent,
    }));
}

function route(definition: RouteDefinition): AppRouteConfig {
  const title =
    typeof definition.title === 'string' ? definition.title : '页面';

  return {
    ...definition,
    component: createLazyRoute(
      definition.importer,
      title,
      definition.skeleton || 'default'
    ),
  };
}

export const appRoutes: AppRouteConfig[] = [
  route({
    path: '/',
    title: '行情工作台',
    importer: toDefaultExport(
      () => import('@/features/dashboard'),
      'MarketShortcutsPage'
    ),
    skeleton: 'studio',
    preload: true,
    nav: {
      label: '行情',
      icon: MarketWorkbenchIcon,
      group: MAIN_GROUP,
      order: 10,
    },
  }),
  route({
    path: '/market-shortcuts',
    title: '行情工作台',
    importer: toDefaultExport(
      () => import('./redirects'),
      'MarketWorkbenchRedirect'
    ),
    skeleton: 'default',
  }),
  route({
    path: '/holdings',
    title: '持仓',
    importer: toDefaultExport(
      () => import('@/features/trading'),
      'TradingPage'
    ),
    skeleton: 'dashboard',
    preload: true,
    nav: {
      label: '持仓',
      icon: PortfolioHoldingsIcon,
      group: MAIN_GROUP,
      order: 20,
    },
  }),
  route({
    path: '/account',
    title: '账户概览',
    importer: toDefaultExport(
      () => import('@/features/account'),
      'AccountPage'
    ),
    skeleton: 'dashboard',
  }),
  route({
    path: '/entry-plans',
    title: '买入管理',
    importer: toDefaultExport(
      () => import('@/features/entry-plans'),
      'ConnectedEntryPlansPage'
    ),
    skeleton: 'dashboard',
    nav: {
      label: '买入管理',
      icon: BuyManagementIcon,
      group: MAIN_GROUP,
      order: 25,
    },
  }),
  route({
    path: '/t-trade',
    title: '做T助手',
    importer: toDefaultExport(
      () => import('@/features/portfolio'),
      'TTradeGlobalPage'
    ),
    skeleton: 'dashboard',
    nav: {
      label: '做T助手',
      icon: TTradeCycleIcon,
      group: MAIN_GROUP,
      order: 30,
    },
  }),
  route({
    path: '/limit-up-board',
    title: '打板助手',
    importer: toDefaultExport(
      () => import('@/features/strategies'),
      'LimitUpBoardPage'
    ),
    skeleton: 'dashboard',
    preload: true,
    nav: {
      label: '打板助手',
      icon: LimitUpBoardIcon,
      group: MAIN_GROUP,
      order: 35,
    },
  }),
  route({
    path: '/liquidation',
    title: '卖出管理',
    importer: toDefaultExport(
      () => import('@/features/portfolio'),
      'LiquidationPage'
    ),
    skeleton: 'table',
    nav: {
      label: '卖出管理',
      icon: SellManagementIcon,
      group: MAIN_GROUP,
      order: 40,
    },
  }),
  route({
    path: '/strategies',
    title: '策略管理',
    importer: toDefaultExport(
      () => import('@/features/strategies'),
      'StrategiesPage'
    ),
    skeleton: 'dashboard',
    preload: true,
    nav: {
      label: '策略管理',
      icon: StrategyGraphIcon,
      group: MAIN_GROUP,
      order: 50,
    },
  }),
  route({
    path: '/strategies/run',
    title: '策略管理',
    importer: toDefaultExport(
      () => import('@/features/strategies'),
      'StrategyRunPage'
    ),
    skeleton: 'form',
  }),
  route({
    path: '/strategies/:strategyId/run',
    title: '策略管理',
    importer: toDefaultExport(
      () => import('@/features/strategies'),
      'StrategyRunPage'
    ),
    skeleton: 'form',
  }),
  route({
    path: '/strategies/:strategyId/runs/:runId',
    title: '策略管理',
    importer: toDefaultExport(
      () => import('@/features/strategies'),
      'StrategyDetailPage'
    ),
    skeleton: 'detail',
  }),
  route({
    path: '/strategies/:strategyId',
    title: '策略管理',
    importer: toDefaultExport(
      () => import('@/features/strategies'),
      'StrategyDetailPage'
    ),
    skeleton: 'detail',
  }),
  route({
    path: '/research/:studyId/:version/runs/:runId',
    title: pathname => {
      const runId = normalizePath(pathname).split('/').filter(Boolean)[4];
      if (!runId) return '研究详情';
      const decoded = safeDecodeURIComponent(runId);
      const suffix = decoded.length > 12 ? decoded.slice(-12) : decoded;
      return `研究 ${suffix}`;
    },
    importer: toDefaultExport(
      () => import('@/features/research'),
      'ResearchRunDetailPage'
    ),
    skeleton: 'detail',
  }),
  route({
    path: '/research',
    title: '研究中心',
    importer: toDefaultExport(
      () => import('@/features/research'),
      'ResearchCenterPage'
    ),
    skeleton: 'table',
    nav: {
      label: '研究中心',
      icon: MarketResearchIcon,
      group: MAIN_GROUP,
      order: 55,
    },
  }),
  route({
    path: '/screening',
    title: '股票筛选器',
    importer: toDefaultExport(
      () => import('@/features/screening'),
      'StockScreeningPage'
    ),
    skeleton: 'table',
    nav: {
      label: '股票筛选',
      icon: StockScreeningIcon,
      group: MAIN_GROUP,
      order: 60,
    },
  }),
  route({
    path: '/stock/:stockCode',
    title: '个股详情',
    importer: toDefaultExport(
      () => import('@/features/stocks'),
      'StockDetailPage'
    ),
    skeleton: 'detail',
  }),
  route({
    path: '/settings',
    title: '系统设置',
    importer: toDefaultExport(
      () => import('@/features/settings'),
      'SystemSettingsPage'
    ),
    skeleton: 'dashboard',
    nav: {
      label: '系统设置',
      icon: ControlSettingsIcon,
      group: SETTINGS_GROUP,
      order: 10,
    },
  }),
  route({
    path: '/settings/qmt',
    title: '系统设置',
    importer: toDefaultExport(
      () => import('@/features/settings'),
      'SystemSettingsPage'
    ),
    skeleton: 'dashboard',
  }),
  route({
    path: '/settings/ai-runtime',
    title: '系统设置',
    importer: toDefaultExport(
      () => import('@/features/settings'),
      'SystemSettingsPage'
    ),
    skeleton: 'dashboard',
  }),
  route({
    path: '/settings/agents',
    title: '系统设置',
    importer: toDefaultExport(
      () => import('./redirects'),
      'LegacyAgentSettingsRedirect'
    ),
    skeleton: 'default',
  }),
  route({
    path: '/settings/data',
    title: '数据管理门户',
    importer: toDefaultExport(
      () => import('@/features/system'),
      'DataManagementPage'
    ),
    skeleton: 'dashboard',
  }),
  route({
    path: '/settings/data/market',
    title: '全市场数据',
    importer: toDefaultExport(
      () => import('@/features/system'),
      'ComprehensiveMarketPage'
    ),
    skeleton: 'dashboard',
  }),
  route({
    path: '/settings/data/stocks',
    title: '个股数据',
    importer: toDefaultExport(
      () => import('@/features/system'),
      'StockDataIndexPage'
    ),
    skeleton: 'dashboard',
  }),
  route({
    path: '/settings/data/sectors',
    title: '板块数据管理',
    importer: toDefaultExport(
      () => import('@/features/system'),
      'SectorDataPage'
    ),
    skeleton: 'table',
  }),
  route({
    path: '/settings/data/holdings',
    title: '持仓数据同步',
    importer: toDefaultExport(
      () => import('@/features/system'),
      'HoldingsDataSyncPage'
    ),
    skeleton: 'table',
  }),
  route({
    path: '/settings/data/calendar',
    title: '交易日历',
    importer: toDefaultExport(
      () => import('@/features/system'),
      'TradingCalendarPage'
    ),
    skeleton: 'dashboard',
  }),
  route({
    path: '/settings/data/reverse-repo',
    title: '逆回购数据',
    importer: toDefaultExport(
      () => import('@/features/system'),
      'ReverseRepoDataPage'
    ),
    skeleton: 'table',
  }),
  route({
    path: '/settings/data/transactions',
    title: '交易流水数据',
    importer: toDefaultExport(
      () => import('@/features/system'),
      'TransactionDataPage'
    ),
    skeleton: 'table',
  }),
  route({
    path: '/settings/data/financial',
    title: '财务数据',
    importer: toDefaultExport(
      () => import('@/features/system'),
      'FinancialDataPage'
    ),
    skeleton: 'table',
  }),
  route({
    path: '/settings/data/market-data',
    title: 'K线批量同步',
    importer: toDefaultExport(
      () => import('@/features/system'),
      'DailyMarketDataSyncPage'
    ),
    skeleton: 'dashboard',
  }),
  route({
    path: '/settings/data/announcements',
    title: '公告同步',
    importer: toDefaultExport(
      () => import('@/features/system'),
      'AnnouncementSyncPage'
    ),
    skeleton: 'dashboard',
  }),
  route({
    path: '/settings/data/:stockCode',
    title: '数据详情',
    importer: toDefaultExport(
      () => import('@/features/system'),
      'StockDataDetailPage'
    ),
    skeleton: 'detail',
  }),
  route({
    path: '/system/flow-runs/:id',
    title: '任务详情',
    importer: toDefaultExport(
      () => import('@/features/system'),
      'FlowRunDetailPage'
    ),
    skeleton: 'detail',
  }),
];

export function normalizePath(pathname: string): string {
  const pathOnly = pathname.split(/[?#]/)[0] || '/';
  if (pathOnly === '/') return '/';
  return pathOnly.replace(/\/+$/, '');
}

export function safeDecodeURIComponent(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export function matchAppRoute(pattern: string, pathname: string): boolean {
  const normalizedPattern = normalizePath(pattern);
  const normalizedPathname = normalizePath(pathname);

  if (normalizedPattern === '/') return normalizedPathname === '/';

  const patternParts = normalizedPattern.split('/').filter(Boolean);
  const pathnameParts = normalizedPathname.split('/').filter(Boolean);

  if (patternParts.length !== pathnameParts.length) return false;

  return patternParts.every((part, index) => {
    if (part.startsWith(':')) return pathnameParts[index].length > 0;
    return part === pathnameParts[index];
  });
}

export function findRoute(pathname: string): AppRouteConfig | undefined {
  return appRoutes.find(routeConfig =>
    matchAppRoute(routeConfig.path, pathname)
  );
}

export function getPageTitle(pathname: string): string {
  const routeConfig = findRoute(pathname);
  if (!routeConfig) return '页面';
  return typeof routeConfig.title === 'function'
    ? routeConfig.title(pathname)
    : routeConfig.title;
}

export function isNavigationItemActive(
  href: string,
  pathname: string
): boolean {
  const normalizedHref = normalizePath(href);
  const normalizedPathname = normalizePath(pathname);

  if (normalizedHref === '/') return normalizedPathname === '/';
  if (normalizedHref === '/settings') {
    return [
      '/settings',
      '/settings/qmt',
      '/settings/ai-runtime',
      '/settings/agents',
    ].includes(normalizedPathname);
  }
  return (
    normalizedPathname === normalizedHref ||
    normalizedPathname.startsWith(`${normalizedHref}/`)
  );
}

export function getStudioNavigation(): NavigationGroup[] {
  const groups = new Map<string, NavigationItem[]>();

  appRoutes.forEach(routeConfig => {
    if (!routeConfig.nav) return;
    const groupName = routeConfig.nav.group || MAIN_GROUP;
    const group = groups.get(groupName) || [];
    group.push({
      label: routeConfig.nav.label,
      href: routeConfig.path,
      icon: routeConfig.nav.icon,
      order: routeConfig.nav.order || 0,
    });
    groups.set(groupName, group);
  });

  return Array.from(groups.entries()).map(([title, items]) => ({
    title,
    items: items.sort((a, b) => a.order - b.order),
  }));
}

export function preloadRoute(pathname: string): Promise<void> | undefined {
  const routeConfig =
    appRoutes.find(
      route => normalizePath(route.path) === normalizePath(pathname)
    ) || findRoute(pathname);

  return routeConfig ? preloadImporter(routeConfig.importer) : undefined;
}

export function preloadImportantRoutes(): void {
  const preload = () => {
    appRoutes
      .filter(routeConfig => routeConfig.preload && routeConfig.path !== '/')
      .forEach(routeConfig => {
        void preloadImporter(routeConfig.importer);
      });
  };

  if (typeof window.requestIdleCallback === 'function') {
    window.requestIdleCallback(preload, { timeout: 2500 });
    return;
  }

  window.setTimeout(preload, 1200);
}
