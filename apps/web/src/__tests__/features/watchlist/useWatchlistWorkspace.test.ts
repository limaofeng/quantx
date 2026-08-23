import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useWatchlistWorkspace } from '@/features/watchlist/hooks/useWatchlistWorkspace';

const mocks = vi.hoisted(() => ({
  mutationIndex: 0,
  mutations: Array.from({ length: 8 }, () => vi.fn()),
  refresh: vi.fn(),
  useMutation: vi.fn(),
  useQuery: vi.fn(),
}));

vi.mock('urql', () => ({
  useMutation: mocks.useMutation,
  useQuery: mocks.useQuery,
}));

vi.mock('@/features/watchlist/graphql/watchlistOperations', () => ({
  CreateWatchlistGroupMutation: 'CreateWatchlistGroupMutation',
  DeleteWatchlistGroupMutation: 'DeleteWatchlistGroupMutation',
  RemoveWatchlistItemMutation: 'RemoveWatchlistItemMutation',
  RenameWatchlistGroupMutation: 'RenameWatchlistGroupMutation',
  ReorderWatchlistGroupItemsMutation: 'ReorderWatchlistGroupItemsMutation',
  ReorderWatchlistGroupsMutation: 'ReorderWatchlistGroupsMutation',
  ReorderWatchlistItemsMutation: 'ReorderWatchlistItemsMutation',
  SaveWatchlistItemMutation: 'SaveWatchlistItemMutation',
  WatchlistWorkspaceQuery: 'WatchlistWorkspaceQuery',
}));

describe('useWatchlistWorkspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.mutationIndex = 0;
    mocks.useQuery.mockReturnValue([
      {
        data: {
          watchlist: [],
          watchlistGroups: [],
        },
        error: undefined,
        fetching: false,
        stale: false,
      },
      mocks.refresh,
    ]);
    mocks.useMutation.mockImplementation(() => {
      const execute = mocks.mutations[mocks.mutationIndex];
      mocks.mutationIndex += 1;
      return [{ error: undefined }, execute];
    });
    for (const [index, mutation] of mocks.mutations.entries()) {
      const field = [
        'saveWatchlistItem',
        'removeWatchlistItem',
        'createWatchlistGroup',
        'renameWatchlistGroup',
        'deleteWatchlistGroup',
        'reorderWatchlistItems',
        'reorderWatchlistGroups',
        'reorderWatchlistGroupItems',
      ][index];
      mutation.mockResolvedValue({
        data: { [field]: { message: 'ok', success: true } },
        error: undefined,
      });
    }
  });

  it('normalizes save groups and forwards every ordering id to typed mutations', async () => {
    const { result } = renderHook(() => useWatchlistWorkspace('account-1'));

    await act(async () => {
      await result.current.saveItem({
        groupIds: ['group-a', 'group-a', 'group-b'],
        instrumentName: '平安银行',
        stockCode: ' 000001.sz ',
      });
      await result.current.reorderItems({ itemIds: ['item-2', 'item-1'] });
      await result.current.reorderGroups({ groupIds: ['group-b', 'group-a'] });
      await result.current.reorderGroupItems({
        groupId: 'group-b',
        itemIds: ['item-1', 'item-2'],
      });
    });

    expect(mocks.mutations[0]).toHaveBeenCalledWith({
      input: {
        accountId: 'account-1',
        groupIds: ['group-a', 'group-b'],
        instrumentName: '平安银行',
        stockCode: '000001.SZ',
      },
    });
    expect(mocks.mutations[5]).toHaveBeenCalledWith({
      input: { accountId: 'account-1', itemIds: ['item-2', 'item-1'] },
    });
    expect(mocks.mutations[6]).toHaveBeenCalledWith({
      input: { accountId: 'account-1', groupIds: ['group-b', 'group-a'] },
    });
    expect(mocks.mutations[7]).toHaveBeenCalledWith({
      input: {
        accountId: 'account-1',
        groupId: 'group-b',
        itemIds: ['item-1', 'item-2'],
      },
    });
  });
});
