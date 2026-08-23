import {
  buildStudioWorkspaceTab,
  getStudioWorkspaceTabId,
  mergeStudioWorkspaceTab,
  normalizeStudioWorkspaceTabTitle,
  normalizeStudioWorkspaceTabs,
  setStudioWorkspaceTabPinned,
} from '@/components/studio-workspace';

describe('studio workspace tabs', () => {
  it('labels the screening workspace as stock selection', () => {
    expect(buildStudioWorkspaceTab('/screening')).toMatchObject({
      name: '选股',
      path: '/screening',
    });
  });

  it('keeps watchlist symbol switches in one stable studio tab', () => {
    expect(
      buildStudioWorkspaceTab('/watchlist?collection=all&symbol=000001.SZ')
    ).toMatchObject({
      id: 'watchlist',
      name: '自选',
      path: '/watchlist?collection=all&symbol=000001.SZ',
    });
    expect(
      getStudioWorkspaceTabId('/watchlist?collection=holdings&symbol=600519.SH')
    ).toBe('watchlist');
  });

  it('builds one holdings tab id regardless of selected symbol', () => {
    expect(getStudioWorkspaceTabId('/holdings?symbol=562500.SH')).toBe(
      'holdings'
    );
    expect(getStudioWorkspaceTabId('/holdings?symbol=600900.sh')).toBe(
      'holdings'
    );
    expect(getStudioWorkspaceTabId('/holdings')).toBe('holdings');
  });

  it('labels holdings tabs without the active symbol', () => {
    expect(buildStudioWorkspaceTab('/holdings?symbol=562500.SH')?.name).toBe(
      '持仓'
    );
    expect(buildStudioWorkspaceTab('/holdings')?.name).toBe('持仓');
  });

  it('hosts every authenticated route in the Studio workspace', () => {
    const agentTab = buildStudioWorkspaceTab('/settings/agents');
    const overviewTab = buildStudioWorkspaceTab('/settings');
    const runtimeTab = buildStudioWorkspaceTab('/settings/ai-runtime');
    const unknownTab = buildStudioWorkspaceTab('/missing-page');

    expect(agentTab.name).toBe('系统设置');
    expect(agentTab.path).toBe('/settings/agents');
    expect(agentTab.id).toBe('settings');
    expect(overviewTab.id).toBe('settings');
    expect(runtimeTab.id).toBe('settings');
    expect(unknownTab.name).toBe('页面未找到');
  });

  it('hosts the market workbench as the default studio workspace', () => {
    const tab = buildStudioWorkspaceTab('/');
    const legacyTab = buildStudioWorkspaceTab('/market-shortcuts');

    expect(tab?.id).toBe('page:/');
    expect(tab?.name).toBe('行情工作台');
    expect(tab?.path).toBe('/');
    expect(legacyTab).toEqual(tab);
  });

  it('hosts the global T trade assistant in the studio workspace', () => {
    const tab = buildStudioWorkspaceTab('/t-trade');

    expect(tab?.id).toBe('page:/t-trade');
    expect(tab?.name).toBe('做T助手');
    expect(tab?.path).toBe('/t-trade');
  });

  it('hosts the limit-up board assistant in the studio workspace', () => {
    const tab = buildStudioWorkspaceTab('/limit-up-board');

    expect(tab?.id).toBe('page:/limit-up-board');
    expect(tab?.name).toBe('打板助手');
    expect(tab?.path).toBe('/limit-up-board');
  });

  it('gives research list and run details meaningful workspace tabs', () => {
    const listTab = buildStudioWorkspaceTab('/research');
    const detailTab = buildStudioWorkspaceTab(
      '/research/volume-shock/smoke-v1/runs/20260729-211642-718ab13b?key=opaque-key'
    );

    expect(listTab.name).toBe('研究中心');
    expect(detailTab.name).toBe('研究 642-718ab13b');
    expect(detailTab.path).toContain('?key=opaque-key');
  });

  it('keeps malformed research run ids safe in workspace titles', () => {
    expect(
      buildStudioWorkspaceTab('/research/volume-shock/v1/runs/%?key=opaque-key')
        .name
    ).toBe('研究 %');
  });

  it('keeps every account view in one stable workspace tab', () => {
    const overview = buildStudioWorkspaceTab('/account?view=overview');
    const trades = buildStudioWorkspaceTab('/account?view=trades');
    const closed = buildStudioWorkspaceTab('/account?view=closed');

    expect(overview?.id).toBe('account');
    expect(trades?.id).toBe('account');
    expect(closed?.id).toBe('account');
    expect(trades?.name).toBe('账户概览');
    expect(mergeStudioWorkspaceTab([overview!], trades!)).toEqual([trades]);
  });

  it('merges selected holdings symbols into the same tab', () => {
    const firstTab = buildStudioWorkspaceTab('/holdings?symbol=562500.SH')!;
    const duplicateTab = buildStudioWorkspaceTab(
      '/holdings?symbol=562500.sh&mode=buy'
    )!;
    const secondTab = buildStudioWorkspaceTab('/holdings?symbol=600900.SH')!;

    const withDuplicate = mergeStudioWorkspaceTab([firstTab], duplicateTab);
    const withSecond = mergeStudioWorkspaceTab(withDuplicate, secondTab);

    expect(withDuplicate).toHaveLength(1);
    expect(withDuplicate[0].id).toBe('holdings');
    expect(withDuplicate[0].path).toBe('/holdings?symbol=562500.sh&mode=buy');
    expect(withSecond).toHaveLength(1);
    expect(withSecond[0].id).toBe('holdings');
    expect(withSecond[0].path).toBe('/holdings?symbol=600900.SH');
  });

  it('keeps stock information in a separate tab', () => {
    const holdingsTab = buildStudioWorkspaceTab('/holdings?symbol=601318.SH')!;
    const stockTab = buildStudioWorkspaceTab('/stock/601318.SH')!;

    expect(
      mergeStudioWorkspaceTab([holdingsTab], stockTab).map(tab => tab.id)
    ).toEqual(['holdings', 'stock:601318.SH']);
  });

  it('reuses one preview slot for transient detail pages', () => {
    const holdingsTab = buildStudioWorkspaceTab('/holdings')!;
    const firstStockTab = buildStudioWorkspaceTab('/stock/601318.SH')!;
    const secondStockTab = buildStudioWorkspaceTab('/stock/002594.SZ')!;

    expect(firstStockTab.isPreview).toBe(true);
    expect(firstStockTab.isPreviewable).toBe(true);
    expect(
      mergeStudioWorkspaceTab([holdingsTab, firstStockTab], secondStockTab).map(
        tab => tab.id
      )
    ).toEqual(['holdings', 'stock:002594.SZ']);
  });

  it('keeps a pinned detail tab when a new preview opens', () => {
    const firstStockTab = buildStudioWorkspaceTab('/stock/601318.SH')!;
    const pinnedTabs = setStudioWorkspaceTabPinned(
      [firstStockTab],
      firstStockTab.id,
      true
    );
    const secondStockTab = buildStudioWorkspaceTab('/stock/002594.SZ')!;
    const mergedTabs = mergeStudioWorkspaceTab(pinnedTabs, secondStockTab);

    expect(pinnedTabs[0].isPreview).toBe(false);
    expect(mergedTabs.map(tab => tab.id)).toEqual([
      'stock:601318.SH',
      'stock:002594.SZ',
    ]);
  });

  it('keeps the market workbench and liquidation as separate workspace tabs', () => {
    const marketTab = buildStudioWorkspaceTab('/')!;
    const liquidationTab = buildStudioWorkspaceTab(
      '/liquidation?symbol=562500.SH'
    )!;

    expect(liquidationTab.id).toBe('liquidation');
    expect(mergeStudioWorkspaceTab([marketTab], liquidationTab)).toEqual([
      marketTab,
      liquidationTab,
    ]);
  });

  it('merges left-click liquidation symbols into the same tab', () => {
    const firstTab = buildStudioWorkspaceTab('/liquidation?symbol=562500.SH')!;
    const secondTab = buildStudioWorkspaceTab('/liquidation?symbol=600900.SH')!;
    const mergedTabs = mergeStudioWorkspaceTab([firstTab], secondTab);

    expect(mergedTabs).toHaveLength(1);
    expect(mergedTabs[0].id).toBe('liquidation');
    expect(mergedTabs[0].path).toBe('/liquidation?symbol=600900.SH');
  });

  it('allows manually opened liquidation tabs through right-click intent', () => {
    const mainTab = buildStudioWorkspaceTab('/liquidation?symbol=562500.SH')!;
    const manualTab = buildStudioWorkspaceTab(
      '/liquidation?symbol=600900.SH&workspaceTab=600900.SH-1&name=%E9%95%BF%E6%B1%9F%E7%94%B5%E5%8A%9B'
    )!;

    expect(manualTab.id).toBe('liquidation:600900.SH-1');
    expect(manualTab.name).toBe('清仓 长江电力');
    expect(
      mergeStudioWorkspaceTab([mainTab], manualTab).map(tab => tab.id)
    ).toEqual(['liquidation', 'liquidation:600900.SH-1']);
  });

  it('falls back to the stock code for manually opened liquidation tabs', () => {
    const manualTab = buildStudioWorkspaceTab(
      '/liquidation?symbol=600900.SH&workspaceTab=600900.SH-1'
    )!;

    expect(manualTab.name).toBe('清仓 600900.SH');
  });

  it('normalizes stale names back to the single holdings title', () => {
    const tab = buildStudioWorkspaceTab('/holdings?symbol=601318.SH')!;

    expect(
      normalizeStudioWorkspaceTabTitle({
        ...tab,
        name: '中国平安',
      }).name
    ).toBe('持仓');

    expect(
      normalizeStudioWorkspaceTabTitle({
        ...tab,
        name: '持仓 中国平安 601318.SH',
      }).name
    ).toBe('持仓');
  });

  it('collapses stale symbol-specific holdings tabs', () => {
    const staleTabs = [
      {
        ...buildStudioWorkspaceTab('/holdings?symbol=600900.SH')!,
        id: 'holdings:600900.SH',
        name: '持仓 600900.SH',
      },
      {
        ...buildStudioWorkspaceTab('/holdings?symbol=002594.SZ')!,
        id: 'holdings:002594.SZ',
        name: '持仓 002594.SZ',
      },
      buildStudioWorkspaceTab('/stock/002594.SZ')!,
    ];

    expect(normalizeStudioWorkspaceTabs(staleTabs).map(tab => tab.id)).toEqual([
      'holdings',
      'stock:002594.SZ',
    ]);
  });
});
