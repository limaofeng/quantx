import { describe, expect, it } from 'vitest';

import {
  mergeWatchlistGroupIds,
  sortWatchlistItemsForGroup,
} from '@/features/watchlist/utils';

const group = {
  displayOrder: 0,
  id: 'group-1',
  itemCount: 2,
  name: 'Group A',
};

describe('watchlist membership ordering', () => {
  it('keeps main-list order independent from custom-group order', () => {
    const items = [
      {
        displayOrder: 0,
        groupMemberships: [{ displayOrder: 1, groupId: group.id }],
        groups: [group],
        id: 'item-a',
        instrumentName: 'Stock A',
        stockCode: '000001.SZ',
      },
      {
        displayOrder: 1,
        groupMemberships: [{ displayOrder: 0, groupId: group.id }],
        groups: [group],
        id: 'item-b',
        instrumentName: 'Stock B',
        stockCode: '000002.SZ',
      },
    ];

    expect(sortWatchlistItemsForGroup(items, group.id).map(item => item.id)).toEqual([
      'item-b',
      'item-a',
    ]);
    expect(items.slice().sort((left, right) => left.displayOrder - right.displayOrder).map(item => item.id)).toEqual([
      'item-a',
      'item-b',
    ]);
  });

  it('preserves existing groups and unions a newly selected group', () => {
    const item = {
      displayOrder: 0,
      groupMemberships: [],
      groups: [
        { ...group, id: 'group-a', name: 'Group A' },
        { ...group, id: 'group-b', name: 'Group B' },
      ],
      id: 'item-a',
      instrumentName: 'Stock A',
      stockCode: '000001.SZ',
    };

    expect(mergeWatchlistGroupIds(item)).toEqual(['group-a', 'group-b']);
    expect(mergeWatchlistGroupIds(item, 'group-c')).toEqual([
      'group-a',
      'group-b',
      'group-c',
    ]);
  });
});
