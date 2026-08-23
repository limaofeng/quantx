import { useEffect, useRef } from 'react';

import {
  normalizeWatchlistCode,
  useWatchlistWorkspace,
} from '@/features/watchlist/hooks';
import type { WatchlistSaveInput } from '@/features/watchlist/types';
import { STORAGE_KEYS } from '@/shared/constants/app';

const LOCAL_WATCHLIST_MIGRATED_KEY = `${STORAGE_KEYS.WATCHLIST}:migrated`;

/**
 * Shared account watchlist facade.
 *
 * Existing callers only need the codes for cache warm-up and data filters;
 * feature pages use the typed multi-group methods from useWatchlistWorkspace.
 */
export function useWatchlist(accountId?: string | null) {
  const workspace = useWatchlistWorkspace(accountId);
  const migrationAttemptedRef = useRef(false);

  useEffect(() => {
    if (workspace.fetching || migrationAttemptedRef.current) return;
    if (typeof window === 'undefined') return;
    if (workspace.items.length > 0) return;
    if (window.localStorage.getItem(LOCAL_WATCHLIST_MIGRATED_KEY) === 'true') {
      return;
    }

    const localCodes = readLocalWatchlistCodes();
    if (localCodes.length === 0) {
      window.localStorage.setItem(LOCAL_WATCHLIST_MIGRATED_KEY, 'true');
      return;
    }

    migrationAttemptedRef.current = true;
    void Promise.all(
      localCodes.map(stockCode =>
        workspace
          .saveItem({
            accountId,
            groupIds: [],
            stockCode,
          })
          .catch(() => null)
      )
    ).then(results => {
      if (results.every(Boolean)) {
        window.localStorage.setItem(LOCAL_WATCHLIST_MIGRATED_KEY, 'true');
      }
    });
  }, [accountId, workspace]);

  return workspace;
}

export type SharedWatchlistSaveInput = WatchlistSaveInput;

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
      values.map(value => normalizeWatchlistCode(value || '')).filter(Boolean)
    )
  );
}
