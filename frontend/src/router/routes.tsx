import {
  BarChart3,
  Bot,
  Briefcase,
  Database,
  Filter,
  Hand,
  LayoutDashboard,
  Radar,
  type LucideIcon,
} from 'lucide-react';

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

export interface RouteMobileNavConfig {
  label: string;
  icon: LucideIcon;
  order?: number;
}

export interface AppRouteConfig {
  path: string;
  title: string | ((pathname: string) => string);
  component: RouteComponent;
  importer: RouteImporter;
  skeleton?: RouteSkeletonVariant;
  nav?: RouteNavConfig;
  mobileNav?: RouteMobileNavConfig;
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
    title: '仪表板',
    importer: toDefaultExport(
      () => import('@/features/dashboard'),
      'DashboardPage'
    ),
    skeleton: 'dashboard',
    preload: true,
    nav: {
      label: '仪表板',
      icon: LayoutDashboard,
      group: MAIN_GROUP,
      order: 10,
    },
    mobileNav: { label: '仪表板', icon: LayoutDashboard, order: 10 },
  }),
  route({
    path: '/market-shortcuts',
    title: '行情快捷方式',
    importer: toDefaultExport(
      () => import('@/features/dashboard'),
      'MarketShortcutsPage'
    ),
    skeleton: 'studio',
    preload: true,
    nav: {
      label: '行情',
      icon: BarChart3,
      group: MAIN_GROUP,
      order: 15,
    },
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
      icon: Briefcase,
      group: MAIN_GROUP,
      order: 20,
    },
    mobileNav: { label: '持仓', icon: Briefcase, order: 20 },
  }),
  route({
    path: '/account',
    title: '账户中心',
    importer: toDefaultExport(
      () => import('@/features/account'),
      'AccountPage'
    ),
    skeleton: 'dashboard',
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
      icon: Radar,
      group: MAIN_GROUP,
      order: 30,
    },
    mobileNav: { label: '做T', icon: Radar, order: 30 },
  }),
  route({
    path: '/liquidation',
    title: '清仓管理',
    importer: toDefaultExport(
      () => import('@/features/portfolio'),
      'LiquidationPage'
    ),
    skeleton: 'table',
    nav: {
      label: '清仓管理',
      icon: Hand,
      group: MAIN_GROUP,
      order: 40,
    },
    mobileNav: { label: '清仓', icon: Hand, order: 40 },
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
      icon: Bot,
      group: MAIN_GROUP,
      order: 50,
    },
    mobileNav: { label: '策略', icon: Bot, order: 50 },
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
    path: '/screening',
    title: '股票筛选器',
    importer: toDefaultExport(
      () => import('@/features/screening'),
      'StockScreeningPage'
    ),
    skeleton: 'table',
    nav: {
      label: '股票筛选',
      icon: Filter,
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
    path: '/settings/data',
    title: '数据管理门户',
    importer: toDefaultExport(
      () => import('@/features/system'),
      'DataManagementPage'
    ),
    skeleton: 'dashboard',
    nav: {
      label: '数据管理',
      icon: Database,
      group: SETTINGS_GROUP,
      order: 10,
    },
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
  if (!routeConfig) return '仪表板';
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
  return (
    normalizedPathname === normalizedHref ||
    normalizedPathname.startsWith(`${normalizedHref}/`)
  );
}

export function getDesktopNavigation(): NavigationGroup[] {
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

export function getMobileNavigation(): NavigationItem[] {
  return appRoutes
    .filter(routeConfig => routeConfig.mobileNav)
    .map(routeConfig => ({
      label: routeConfig.mobileNav!.label,
      href: routeConfig.path,
      icon: routeConfig.mobileNav!.icon,
      order: routeConfig.mobileNav!.order || 0,
    }))
    .sort((a, b) => a.order - b.order);
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
