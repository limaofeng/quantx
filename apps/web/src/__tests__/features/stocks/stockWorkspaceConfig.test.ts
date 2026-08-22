import { describe, expect, it } from 'vitest';

import {
  detailWorkspaceModes,
  holdingsWorkspaceModes,
} from '@/features/stocks/components/stockWorkspaceConfig';

describe('stock workspace configuration', () => {
  it('keeps the standalone stock page research-first', () => {
    expect(detailWorkspaceModes.map(mode => mode.id)).toEqual([
      'OVERVIEW',
      'CHART',
      'ANNOUNCEMENTS',
      'FINANCIAL',
      'TRADING',
    ]);
  });

  it('keeps the holdings workspace trading-first', () => {
    expect(holdingsWorkspaceModes.map(mode => mode.id)).toEqual([
      'CHART',
      'ORDER',
      'ORDERS',
      'TRADES',
      'ACCOUNT',
    ]);
  });
});
