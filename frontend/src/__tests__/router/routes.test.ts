import {
  appRoutes,
  getDesktopNavigation,
  getMobileNavigation,
  getPageTitle,
  isNavigationItemActive,
  matchAppRoute,
  normalizePath,
} from '@/router';

describe('router configuration', () => {
  it('normalizes trailing slashes and query strings', () => {
    expect(normalizePath('/strategies/123/?tab=logs')).toBe('/strategies/123');
    expect(normalizePath('/')).toBe('/');
  });

  it('matches exact and dynamic route patterns', () => {
    expect(matchAppRoute('/', '/')).toBe(true);
    expect(matchAppRoute('/', '/holdings')).toBe(false);
    expect(matchAppRoute('/stock/:stockCode', '/stock/000001')).toBe(true);
    expect(matchAppRoute('/stock/:stockCode', '/stock/000001/detail')).toBe(
      false
    );
    expect(
      matchAppRoute(
        '/strategies/:strategyId/runs/:runId',
        '/strategies/12/runs/run-1'
      )
    ).toBe(true);
  });

  it('resolves titles from the unified route source', () => {
    expect(getPageTitle('/')).toBe('仪表板');
    expect(getPageTitle('/holdings')).toBe('持仓');
    expect(getPageTitle('/account')).toBe('账户中心');
    expect(getPageTitle('/t-trade')).toBe('做T助手');
    expect(getPageTitle('/stock/000001')).toBe('个股详情');
    expect(getPageTitle('/settings/data/market')).toBe('全市场数据');
    expect(getPageTitle('/settings/data/stocks')).toBe('个股数据');
    expect(getPageTitle('/settings/data/000001')).toBe('数据详情');
    expect(getPageTitle('/system/flow-runs/flow-1')).toBe('任务详情');
  });

  it('keeps specific routes before dynamic fallbacks', () => {
    const indexOf = (path: string) =>
      appRoutes.findIndex(route => route.path === path);

    expect(indexOf('/strategies/run')).toBeLessThan(
      indexOf('/strategies/:strategyId')
    );
    expect(indexOf('/strategies/:strategyId/runs/:runId')).toBeLessThan(
      indexOf('/strategies/:strategyId')
    );
    expect(indexOf('/settings/data/market')).toBeLessThan(
      indexOf('/settings/data/:stockCode')
    );
    expect(indexOf('/settings/data/stocks')).toBeLessThan(
      indexOf('/settings/data/:stockCode')
    );
    expect(indexOf('/settings/data/financial')).toBeLessThan(
      indexOf('/settings/data/:stockCode')
    );
    expect(indexOf('/trading')).toBe(-1);
  });

  it('builds desktop navigation groups from route metadata', () => {
    const navigation = getDesktopNavigation();
    const main = navigation.find(group => group.title === '主菜单');
    const settings = navigation.find(group => group.title === '系统设置');

    expect(main?.items.map(item => item.href)).toEqual([
      '/',
      '/market-shortcuts',
      '/holdings',
      '/t-trade',
      '/liquidation',
      '/strategies',
      '/screening',
    ]);
    expect(settings?.items.map(item => item.href)).toEqual(['/settings/data']);
    expect(main?.items.map(item => item.href)).not.toContain('/account');
  });

  it('builds mobile navigation from route metadata', () => {
    expect(getMobileNavigation().map(item => item.href)).toEqual([
      '/',
      '/holdings',
      '/t-trade',
      '/liquidation',
      '/strategies',
    ]);
  });

  it('marks parent navigation active for child routes', () => {
    expect(isNavigationItemActive('/strategies', '/strategies/12')).toBe(true);
    expect(
      isNavigationItemActive('/settings/data', '/settings/data/market')
    ).toBe(true);
    expect(isNavigationItemActive('/', '/settings/data')).toBe(false);
  });
});
