import {
  appRoutes,
  getPageTitle,
  getStudioNavigation,
  isNavigationItemActive,
  matchAppRoute,
  normalizePath,
  safeDecodeURIComponent,
} from '@/router';

describe('router configuration', () => {
  it('normalizes trailing slashes and query strings', () => {
    expect(normalizePath('/strategies/123/?tab=logs')).toBe('/strategies/123');
    expect(normalizePath('/')).toBe('/');
  });

  it('does not throw on malformed encoded route segments', () => {
    expect(safeDecodeURIComponent('%')).toBe('%');
    expect(getPageTitle('/research/volume-shock/smoke-v1/runs/%')).toBe(
      '研究 %'
    );
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
    expect(
      matchAppRoute(
        '/research/:studyId/:version/runs/:runId',
        '/research/volume-shock/smoke-v1/runs/run-1'
      )
    ).toBe(true);
  });

  it('resolves titles from the unified route source', () => {
    expect(getPageTitle('/')).toBe('行情工作台');
    expect(getPageTitle('/market/indices')).toBe('全部指数');
    expect(getPageTitle('/holdings')).toBe('持仓');
    expect(getPageTitle('/watchlist')).toBe('自选');
    expect(getPageTitle('/account')).toBe('账户概览');
    expect(getPageTitle('/entry-plans')).toBe('买入管理');
    expect(getPageTitle('/t-trade')).toBe('做T助手');
    expect(getPageTitle('/limit-up-board')).toBe('打板助手');
    expect(getPageTitle('/stock/000001')).toBe('个股详情');
    expect(getPageTitle('/settings/data/market')).toBe('全市场数据');
    expect(getPageTitle('/settings/data/stocks')).toBe('个股数据');
    expect(getPageTitle('/settings/data/000001')).toBe('数据详情');
    expect(getPageTitle('/system/flow-runs/flow-1')).toBe('任务详情');
    expect(getPageTitle('/research')).toBe('研究中心');
    expect(
      getPageTitle('/research/volume-shock/smoke-v1/runs/20260729-211642')
    ).toBe('研究 60729-211642');
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
    expect(indexOf('/research/:studyId/:version/runs/:runId')).toBeLessThan(
      indexOf('/research')
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
    expect(indexOf('/market/indices')).toBeGreaterThanOrEqual(0);
  });

  it('builds Studio navigation groups from route metadata', () => {
    const navigation = getStudioNavigation();
    const main = navigation.find(group => group.title === '主菜单');
    const settings = navigation.find(group => group.title === '系统设置');

    expect(main?.items.map(item => item.href)).toEqual([
      '/',
      '/watchlist',
      '/holdings',
      '/entry-plans',
      '/t-trade',
      '/limit-up-board',
      '/liquidation',
      '/strategies',
      '/research',
      '/screening',
    ]);
    expect(settings?.items.map(item => item.href)).toEqual(['/settings']);
    expect(main?.items.map(item => item.href)).not.toContain('/account');
    expect(main?.items.map(item => item.label)).toContain('行情');
    expect(main?.items.map(item => item.label)).not.toContain('仪表板');
  });

  it('marks parent navigation active for child routes', () => {
    expect(isNavigationItemActive('/strategies', '/strategies/12')).toBe(true);
    expect(
      isNavigationItemActive('/settings/data', '/settings/data/market')
    ).toBe(true);
    expect(isNavigationItemActive('/settings', '/settings/qmt')).toBe(true);
    expect(isNavigationItemActive('/settings', '/settings/ai-runtime')).toBe(
      true
    );
    expect(isNavigationItemActive('/settings', '/settings/data')).toBe(false);
    expect(isNavigationItemActive('/', '/settings/data')).toBe(false);
  });
});
