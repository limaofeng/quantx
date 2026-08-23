import { useCallback, useMemo } from 'react';
import { useMutation, useQuery } from 'urql';

import type {
  Watchlist_CreateGroupMutation,
  Watchlist_CreateGroupMutationVariables,
  Watchlist_DeleteGroupMutation,
  Watchlist_DeleteGroupMutationVariables,
  Watchlist_RemoveItemMutation,
  Watchlist_RemoveItemMutationVariables,
  Watchlist_RenameGroupMutation,
  Watchlist_RenameGroupMutationVariables,
  Watchlist_ReorderGroupItemsMutation,
  Watchlist_ReorderGroupItemsMutationVariables,
  Watchlist_ReorderGroupsMutation,
  Watchlist_ReorderGroupsMutationVariables,
  Watchlist_ReorderItemsMutation,
  Watchlist_ReorderItemsMutationVariables,
  Watchlist_SaveItemMutation,
  Watchlist_SaveItemMutationVariables,
  Watchlist_WorkspaceQuery,
  Watchlist_WorkspaceQueryVariables,
} from '@/generated/gql/graphql';

import {
  CreateWatchlistGroupMutation,
  DeleteWatchlistGroupMutation,
  RemoveWatchlistItemMutation,
  RenameWatchlistGroupMutation,
  ReorderWatchlistGroupItemsMutation,
  ReorderWatchlistGroupsMutation,
  ReorderWatchlistItemsMutation,
  SaveWatchlistItemMutation,
  WatchlistWorkspaceQuery,
} from '../graphql/watchlistOperations';
import type {
  WatchlistGroupCreateInput,
  WatchlistGroupDeleteInput,
  WatchlistGroupItemOrderInput,
  WatchlistGroupOrderInput,
  WatchlistGroupRenameInput,
  WatchlistOrderInput,
  WatchlistSaveInput,
  WatchlistWorkspaceData,
} from '../types';

export function normalizeWatchlistCode(value: string) {
  return value.trim().toUpperCase();
}

function toError(message: string) {
  return new Error(message || '自选操作失败');
}

export function useWatchlistWorkspace(accountId?: string | null) {
  const variables = useMemo<Watchlist_WorkspaceQueryVariables>(
    () => ({ accountId: accountId || undefined }),
    [accountId]
  );
  const [queryResult, refresh] = useQuery<
    Watchlist_WorkspaceQuery,
    Watchlist_WorkspaceQueryVariables
  >({
    query: WatchlistWorkspaceQuery,
    variables,
    requestPolicy: 'cache-and-network',
  });
  const [, saveMutation] = useMutation<
    Watchlist_SaveItemMutation,
    Watchlist_SaveItemMutationVariables
  >(SaveWatchlistItemMutation);
  const [, removeMutation] = useMutation<
    Watchlist_RemoveItemMutation,
    Watchlist_RemoveItemMutationVariables
  >(RemoveWatchlistItemMutation);
  const [, createGroupMutation] = useMutation<
    Watchlist_CreateGroupMutation,
    Watchlist_CreateGroupMutationVariables
  >(CreateWatchlistGroupMutation);
  const [, renameGroupMutation] = useMutation<
    Watchlist_RenameGroupMutation,
    Watchlist_RenameGroupMutationVariables
  >(RenameWatchlistGroupMutation);
  const [, deleteGroupMutation] = useMutation<
    Watchlist_DeleteGroupMutation,
    Watchlist_DeleteGroupMutationVariables
  >(DeleteWatchlistGroupMutation);
  const [, reorderItemsMutation] = useMutation<
    Watchlist_ReorderItemsMutation,
    Watchlist_ReorderItemsMutationVariables
  >(ReorderWatchlistItemsMutation);
  const [, reorderGroupsMutation] = useMutation<
    Watchlist_ReorderGroupsMutation,
    Watchlist_ReorderGroupsMutationVariables
  >(ReorderWatchlistGroupsMutation);
  const [, reorderGroupItemsMutation] = useMutation<
    Watchlist_ReorderGroupItemsMutation,
    Watchlist_ReorderGroupItemsMutationVariables
  >(ReorderWatchlistGroupItemsMutation);

  const data = useMemo<WatchlistWorkspaceData>(
    () => ({
      groups: queryResult.data?.watchlistGroups ?? [],
      items: queryResult.data?.watchlist ?? [],
    }),
    [queryResult.data]
  );
  const codes = useMemo(
    () => data.items.map(item => normalizeWatchlistCode(item.stockCode)),
    [data.items]
  );

  const refetch = useCallback(() => {
    refresh({ requestPolicy: 'network-only' });
  }, [refresh]);

  const saveItem = useCallback(
    async (input: WatchlistSaveInput) => {
      const normalizedInput: WatchlistSaveInput = {
        ...input,
        accountId: input.accountId ?? accountId ?? undefined,
        groupIds: Array.from(new Set(input.groupIds)),
        stockCode: normalizeWatchlistCode(input.stockCode),
      };
      const result = await saveMutation({ input: normalizedInput });
      if (result.error) throw result.error;
      if (!result.data?.saveWatchlistItem.success) {
        throw toError(result.data?.saveWatchlistItem.message || '保存自选失败');
      }
      refetch();
      return result.data.saveWatchlistItem;
    },
    [accountId, refetch, saveMutation]
  );

  const removeItem = useCallback(
    async (stockCode: string) => {
      const result = await removeMutation({
        accountId: accountId ?? undefined,
        stockCode: normalizeWatchlistCode(stockCode),
      });
      if (result.error) throw result.error;
      if (!result.data?.removeWatchlistItem.success) {
        throw toError(
          result.data?.removeWatchlistItem.message || '移出自选失败'
        );
      }
      refetch();
      return result.data.removeWatchlistItem;
    },
    [accountId, refetch, removeMutation]
  );

  const createGroup = useCallback(
    async (input: WatchlistGroupCreateInput) => {
      const result = await createGroupMutation({
        input: {
          ...input,
          accountId: input.accountId ?? accountId ?? undefined,
          initialStockCodes: (input.initialStockCodes ?? []).map(
            normalizeWatchlistCode
          ),
        },
      });
      if (result.error) throw result.error;
      if (!result.data?.createWatchlistGroup.success) {
        throw toError(
          result.data?.createWatchlistGroup.message || '创建分组失败'
        );
      }
      refetch();
      return result.data.createWatchlistGroup;
    },
    [accountId, createGroupMutation, refetch]
  );

  const renameGroup = useCallback(
    async (input: WatchlistGroupRenameInput) => {
      const result = await renameGroupMutation({
        input: {
          ...input,
          accountId: input.accountId ?? accountId ?? undefined,
        },
      });
      if (result.error) throw result.error;
      if (!result.data?.renameWatchlistGroup.success) {
        throw toError(
          result.data?.renameWatchlistGroup.message || '重命名分组失败'
        );
      }
      refetch();
      return result.data.renameWatchlistGroup;
    },
    [accountId, refetch, renameGroupMutation]
  );

  const deleteGroup = useCallback(
    async (input: WatchlistGroupDeleteInput) => {
      const result = await deleteGroupMutation({
        input: {
          ...input,
          accountId: input.accountId ?? accountId ?? undefined,
        },
      });
      if (result.error) throw result.error;
      if (!result.data?.deleteWatchlistGroup.success) {
        throw toError(
          result.data?.deleteWatchlistGroup.message || '删除分组失败'
        );
      }
      refetch();
      return result.data.deleteWatchlistGroup;
    },
    [accountId, deleteGroupMutation, refetch]
  );

  const reorderItems = useCallback(
    async (input: WatchlistOrderInput) => {
      const result = await reorderItemsMutation({
        input: {
          ...input,
          accountId: input.accountId ?? accountId ?? undefined,
        },
      });
      if (result.error) throw result.error;
      if (!result.data?.reorderWatchlistItems.success) {
        throw toError(
          result.data?.reorderWatchlistItems.message || '保存自选排序失败'
        );
      }
      refetch();
      return result.data.reorderWatchlistItems;
    },
    [accountId, refetch, reorderItemsMutation]
  );

  const reorderGroups = useCallback(
    async (input: WatchlistGroupOrderInput) => {
      const result = await reorderGroupsMutation({
        input: {
          ...input,
          accountId: input.accountId ?? accountId ?? undefined,
        },
      });
      if (result.error) throw result.error;
      if (!result.data?.reorderWatchlistGroups.success) {
        throw toError(
          result.data?.reorderWatchlistGroups.message || '保存分组排序失败'
        );
      }
      refetch();
      return result.data.reorderWatchlistGroups;
    },
    [accountId, refetch, reorderGroupsMutation]
  );

  const reorderGroupItems = useCallback(
    async (input: WatchlistGroupItemOrderInput) => {
      const result = await reorderGroupItemsMutation({
        input: {
          ...input,
          accountId: input.accountId ?? accountId ?? undefined,
        },
      });
      if (result.error) throw result.error;
      if (!result.data?.reorderWatchlistGroupItems.success) {
        throw toError(
          result.data?.reorderWatchlistGroupItems.message ||
            '保存分组内排序失败'
        );
      }
      refetch();
      return result.data.reorderWatchlistGroupItems;
    },
    [accountId, refetch, reorderGroupItemsMutation]
  );

  return {
    ...data,
    codes,
    error: queryResult.error,
    fetching: queryResult.fetching,
    isStale: queryResult.stale,
    refetch,
    saveItem,
    removeItem,
    createGroup,
    renameGroup,
    deleteGroup,
    reorderItems,
    reorderGroups,
    reorderGroupItems,
  };
}
