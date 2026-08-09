import { useCallback, useEffect, useMemo, useRef } from 'react';
import { useMutation, useQuery } from 'urql';

import { gql } from '@/generated/gql';
import { STORAGE_KEYS } from '@/shared/constants/app';

const LOCAL_WATCHLIST_MIGRATED_KEY = `${STORAGE_KEYS.WATCHLIST}:migrated`;

const WATCHLIST_QUERY = gql(`
  query Watchlist_Items($accountId: String) {
    watchlist(accountId: $accountId) {
      id
      accountId
      stockCode
      instrumentName
      displayOrder
      groupName
      note
      createdAt
      updatedAt
    }
  }
`);

const ADD_WATCHLIST_ITEM_MUTATION = gql(`
  mutation Watchlist_AddItem($input: AddWatchlistItemInput!) {
    addWatchlistItem(input: $input) {
      success
      message
      item {
        id
        accountId
        stockCode
        instrumentName
        displayOrder
        groupName
        note
        createdAt
        updatedAt
      }
    }
  }
`);

const REMOVE_WATCHLIST_ITEM_MUTATION = gql(`
  mutation Watchlist_RemoveItem($stockCode: String!, $accountId: String) {
    removeWatchlistItem(stockCode: $stockCode, accountId: $accountId) {
      success
      message
    }
  }
`);

const REPLACE_WATCHLIST_MUTATION = gql(`
  mutation Watchlist_Replace($symbols: [String!]!, $accountId: String) {
    replaceWatchlist(symbols: $symbols, accountId: $accountId) {
      success
      message
      items {
        id
        accountId
        stockCode
        instrumentName
        displayOrder
        groupName
        note
        createdAt
        updatedAt
      }
    }
  }
`);

const REORDER_WATCHLIST_MUTATION = gql(`
  mutation Watchlist_Reorder($input: ReorderWatchlistInput!) {
    reorderWatchlist(input: $input) {
      success
      message
      items {
        id
        accountId
        stockCode
        instrumentName
        displayOrder
        groupName
        note
        createdAt
        updatedAt
      }
    }
  }
`);

export function useWatchlist(accountId?: string) {
  const migrationAttemptedRef = useRef(false);
  const [{ data, fetching, error }, reloadWatchlist] = useQuery({
    query: WATCHLIST_QUERY,
    variables: { accountId },
    requestPolicy: 'cache-and-network',
  });
  const [, addItemMutation] = useMutation(ADD_WATCHLIST_ITEM_MUTATION);
  const [, removeItemMutation] = useMutation(REMOVE_WATCHLIST_ITEM_MUTATION);
  const [, replaceMutation] = useMutation(REPLACE_WATCHLIST_MUTATION);
  const [, reorderMutation] = useMutation(REORDER_WATCHLIST_MUTATION);

  const items = useMemo(() => data?.watchlist ?? [], [data?.watchlist]);
  const codes = useMemo(
    () => uniqueStockCodes(items.map(item => item.stockCode)),
    [items]
  );

  const refetch = useCallback(() => {
    reloadWatchlist({ requestPolicy: 'network-only' });
  }, [reloadWatchlist]);

  const addItem = useCallback(
    async (input: {
      stockCode: string;
      instrumentName?: string | null;
      groupName?: string | null;
      note?: string | null;
    }) => {
      const result = await addItemMutation({
        input: {
          accountId,
          stockCode: input.stockCode,
          instrumentName: input.instrumentName,
          groupName: input.groupName,
          note: input.note,
        },
      });
      refetch();
      return result.data?.addWatchlistItem;
    },
    [accountId, addItemMutation, refetch]
  );

  const removeItem = useCallback(
    async (stockCode: string) => {
      const result = await removeItemMutation({ accountId, stockCode });
      refetch();
      return result.data?.removeWatchlistItem;
    },
    [accountId, refetch, removeItemMutation]
  );

  const replaceItems = useCallback(
    async (symbols: string[]) => {
      const result = await replaceMutation({
        accountId,
        symbols: uniqueStockCodes(symbols),
      });
      refetch();
      return result.data?.replaceWatchlist;
    },
    [accountId, refetch, replaceMutation]
  );

  const reorderItems = useCallback(
    async (symbols: string[]) => {
      const result = await reorderMutation({
        input: { accountId, symbols: uniqueStockCodes(symbols) },
      });
      refetch();
      return result.data?.reorderWatchlist;
    },
    [accountId, refetch, reorderMutation]
  );

  useEffect(() => {
    if (fetching || migrationAttemptedRef.current) return;
    if (typeof window === 'undefined') return;
    if (items.length > 0) return;
    if (window.localStorage.getItem(LOCAL_WATCHLIST_MIGRATED_KEY) === 'true') {
      return;
    }

    const localCodes = readLocalWatchlistCodes();
    if (localCodes.length === 0) {
      window.localStorage.setItem(LOCAL_WATCHLIST_MIGRATED_KEY, 'true');
      return;
    }

    migrationAttemptedRef.current = true;
    replaceItems(localCodes).then(result => {
      if (result?.success) {
        window.localStorage.setItem(LOCAL_WATCHLIST_MIGRATED_KEY, 'true');
      }
    });
  }, [fetching, items.length, replaceItems]);

  return {
    addItem,
    codes,
    error,
    fetching,
    items,
    refetch,
    removeItem,
    reorderItems,
    replaceItems,
  };
}

export function readLocalWatchlistCodes() {
  if (typeof window === 'undefined') return [];

  try {
    const raw = window.localStorage.getItem(STORAGE_KEYS.WATCHLIST);
    if (!raw) return [];

    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];

    return uniqueStockCodes(
      parsed.map(item => {
        if (typeof item === 'string') return item;
        if (item && typeof item === 'object') {
          const record = item as Record<string, unknown>;
          return String(record.stockCode ?? record.code ?? record.id ?? '');
        }
        return '';
      })
    );
  } catch {
    return [];
  }
}

function uniqueStockCodes(values: Array<string | null | undefined>) {
  return Array.from(
    new Set(
      values
        .map(value => value?.trim().toUpperCase())
        .filter((value): value is string => Boolean(value))
    )
  );
}
