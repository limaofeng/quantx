import { createLucideIcon } from 'lucide-react';

/**
 * QuantX Studio navigation icons.
 *
 * These icons intentionally share Lucide's 24px canvas and stroke behavior so
 * they stay crisp alongside the rest of the shell while expressing trading
 * concepts that the generic icon set does not cover precisely.
 */
export const MarketWorkbenchIcon = createLucideIcon('MarketWorkbench', [
  ['path', { d: 'M5 4v16', key: 'left-wick' }],
  [
    'rect',
    { x: '3.5', y: '8', width: '3', height: '5', rx: '0.75', key: 'left' },
  ],
  ['path', { d: 'M12 3v16', key: 'middle-wick' }],
  [
    'rect',
    {
      x: '10.5',
      y: '6',
      width: '3',
      height: '7',
      rx: '0.75',
      key: 'middle',
    },
  ],
  ['path', { d: 'M19 5v16', key: 'right-wick' }],
  [
    'rect',
    {
      x: '17.5',
      y: '11',
      width: '3',
      height: '6',
      rx: '0.75',
      key: 'right',
    },
  ],
]);

export const PortfolioHoldingsIcon = createLucideIcon('PortfolioHoldings', [
  ['path', { d: 'm12 3 9 4.5-9 4.5-9-4.5L12 3Z', key: 'locked-core' }],
  ['path', { d: 'm3 12 9 4.5 9-4.5', key: 'core' }],
  ['path', { d: 'm3 16.5 9 4.5 9-4.5', key: 'swing' }],
]);

export const BuyManagementIcon = createLucideIcon('BuyManagement', [
  ['path', { d: 'M12 3v11', key: 'inbound' }],
  ['path', { d: 'm8 10 4 4 4-4', key: 'arrow' }],
  [
    'path',
    {
      d: 'M5 16v2a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2',
      key: 'position-tray',
    },
  ],
]);

export const TTradeCycleIcon = createLucideIcon('TTradeCycle', [
  ['path', { d: 'M5 8a8 8 0 0 1 13-2l2 2', key: 'buy-cycle' }],
  ['path', { d: 'M20 4v4h-4', key: 'buy-arrow' }],
  ['path', { d: 'M19 16a8 8 0 0 1-13 2l-2-2', key: 'sell-cycle' }],
  ['path', { d: 'M4 20v-4h4', key: 'sell-arrow' }],
  ['path', { d: 'M9 10h6', key: 't-top' }],
  ['path', { d: 'M12 10v5', key: 't-stem' }],
]);

export const LimitUpBoardIcon = createLucideIcon('LimitUpBoard', [
  [
    'rect',
    { x: '3', y: '3', width: '18', height: '18', rx: '2.5', key: 'board' },
  ],
  ['path', { d: 'M6 7h12', key: 'price-limit' }],
  ['path', { d: 'M12 7v11', key: 'wick' }],
  [
    'rect',
    { x: '9.5', y: '10', width: '5', height: '5', rx: '1', key: 'candle' },
  ],
  ['path', { d: 'M8 18h8', key: 'floor' }],
]);

export const SellManagementIcon = createLucideIcon('SellManagement', [
  ['path', { d: 'M12 14V3', key: 'outbound' }],
  ['path', { d: 'm8 7 4-4 4 4', key: 'arrow' }],
  [
    'path',
    {
      d: 'M5 16v2a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2',
      key: 'position-tray',
    },
  ],
]);

export const StrategyGraphIcon = createLucideIcon('StrategyGraph', [
  ['circle', { cx: '12', cy: '5', r: '2.5', key: 'signal' }],
  ['circle', { cx: '6', cy: '18', r: '2.5', key: 'left-intent' }],
  ['circle', { cx: '18', cy: '18', r: '2.5', key: 'right-intent' }],
  ['path', { d: 'M12 7.5v4', key: 'stem' }],
  ['path', { d: 'M12 11.5H6v4', key: 'left-branch' }],
  ['path', { d: 'M12 11.5h6v4', key: 'right-branch' }],
]);

export const MarketResearchIcon = createLucideIcon('MarketResearch', [
  ['circle', { cx: '10.5', cy: '10.5', r: '6.5', key: 'lens' }],
  ['path', { d: 'm15.2 15.2 4.8 4.8', key: 'handle' }],
  ['path', { d: 'm6.8 11.8 2.2-2.6 2 1.7 3.2-4', key: 'price-trace' }],
]);

export const StockScreeningIcon = createLucideIcon('StockScreening', [
  ['path', { d: 'M4 5h16l-6.5 7.5v5L10.5 20v-7.5L4 5Z', key: 'funnel' }],
  ['path', { d: 'M8 8h8', key: 'market-layer' }],
]);

export const MarketDataIcon = createLucideIcon('MarketData', [
  ['ellipse', { cx: '9.5', cy: '5', rx: '6', ry: '2.5', key: 'top' }],
  [
    'path',
    {
      d: 'M3.5 5v5c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5V5',
      key: 'upper-stack',
    },
  ],
  ['path', { d: 'M3.5 10v5c0 1.3 2.2 2.3 5.1 2.5', key: 'lower-stack' }],
  ['path', { d: 'M12 18h2l1.3-3 2 6 1.2-3H21', key: 'data-pulse' }],
]);

export const ControlSettingsIcon = createLucideIcon('ControlSettings', [
  ['path', { d: 'M4 6h5', key: 'top-left' }],
  ['circle', { cx: '11', cy: '6', r: '2', key: 'top-control' }],
  ['path', { d: 'M13 6h7', key: 'top-right' }],
  ['path', { d: 'M4 12h9', key: 'middle-left' }],
  ['circle', { cx: '15', cy: '12', r: '2', key: 'middle-control' }],
  ['path', { d: 'M17 12h3', key: 'middle-right' }],
  ['path', { d: 'M4 18h2', key: 'bottom-left' }],
  ['circle', { cx: '8', cy: '18', r: '2', key: 'bottom-control' }],
  ['path', { d: 'M10 18h10', key: 'bottom-right' }],
]);
